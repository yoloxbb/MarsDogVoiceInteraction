#!/usr/bin/env python3
"""Build the fixed catalog, KWS source and review matrix from the product XLSX.

Run with the project Python; --check detects drift without writing. XLSX is
read with the standard library, so neither runtime nor builds need openpyxl.
Event choices and reviewed aliases live in config/command_catalog_rules.yaml.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from marsdog_voice_interaction.core.command_lexicon import normalize_command_phrase

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read_sheet(path: Path, sheet_name: str) -> dict[int, dict[str, str]]:
    with ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet = next(s for s in workbook.findall("s:sheets/s:sheet", NS)
                     if s.get("name") == sheet_name)
        rel_id = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels if r.get("Id") == rel_id)
        sheet_path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(t.text or "" for t in item.findall(".//s:t", NS))
                      for item in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        root = ET.fromstring(archive.read(sheet_path))
        rows: dict[int, dict[str, str]] = {}
        for row in root.findall("s:sheetData/s:row", NS):
            cells = {}
            for cell in row:
                value = cell.find("s:v", NS)
                text = value.text or "" if value is not None else "".join(
                    t.text or "" for t in cell.findall(".//s:t", NS))
                if cell.get("t") == "s":
                    text = shared[int(text)]
                cells[re.sub(r"\d", "", cell.get("r"))] = text.strip()
            rows[int(row.get("r"))] = cells
        # Materialize merged category/notes cells, not just the top-left cell.
        for merge in root.findall("s:mergeCells/s:mergeCell", NS):
            start, end = merge.get("ref").split(":")
            col, first = re.fullmatch(r"([A-Z]+)(\d+)", start).groups()
            last = int(re.search(r"\d+", end).group())
            for number in range(int(first) + 1, last + 1):
                rows.setdefault(number, {})[col] = rows[int(first)].get(col, "")
        return rows


def phrases_from_cell(text: str) -> list[str]:
    return list(dict.fromkeys(p.strip() for p in re.split(r"[,，、]", text.strip("[] \n")) if p.strip()))


def build() -> tuple[dict, dict]:
    rules = yaml.safe_load((ROOT / "config/command_catalog_rules.yaml").read_text())
    workbook = ROOT / rules["source"]
    rows = read_sheet(workbook, rules["sheet"])
    expected = set(rules["rows"]) | set(rules["non_catalog_rows"])
    actual = {n for n, cells in rows.items() if n > 2 and cells.get("D")}
    if actual != expected:
        raise ValueError(f"Unreviewed workbook rows: {actual ^ expected}")
    catalog = {
        "version": rules["version"], "source_name": rules["source"],
        "source_sheet": rules["sheet"],
        "source_sha256": hashlib.sha256(workbook.read_bytes()).hexdigest(),
        "source_total_row_count": len(actual),
        "source_row_count": len(rules["rows"]), "source_rows": list(rules["rows"]),
        "global_constraints": rows[1]["A"], "expansion": rules["expansion"],
        "commands": [], "phrase_variants": {}, "non_catalog_entries": [],
    }
    for row, rule in rules["rows"].items():
        cells = rows[row]
        command = {k: v for k, v in rule.items() if k not in {"phrases", "exclude_phrases"}}
        command.setdefault("control", "DO")
        command["source_rows"] = [row]
        command["phrases"] = rule.get("phrases", phrases_from_cell(cells["D"]))
        command["phrases"] = [p for p in command["phrases"] if p not in rule.get("exclude_phrases", [])]
        command.update(category=cells.get("B", ""), subcategory=cells.get("C", ""),
                       behavior=cells.get("E", ""), secondary_behavior=cells.get("F", ""),
                       notes=cells.get("G", ""))
        if rule.get("exclude_phrases"):
            bark_row = next(n for n, r in rules["rows"].items() if r["command_key"] == "BARK")
            command["routing_note"] = f"叫两声、说话统一归 BARK（第{bark_row}行）；重复的你叫什么名字合并。"
        catalog["commands"].append(command)
    # XLSX 外的新需求表口令：没有 Excel 行号，词句和说明直接写在审核规则里。
    texts = ("category", "subcategory", "behavior", "secondary_behavior", "notes")
    for rule in rules.get("extra_commands", []):
        command = {k: v for k, v in rule.items() if k != "phrases" and k not in texts}
        command.setdefault("control", "DO")
        command["source_rows"] = []
        command["phrases"] = list(rule["phrases"])
        command.update({k: rule.get(k, "") for k in texts})
        catalog["commands"].append(command)
    # Reject cross-route collisions and omit aliases already supplied by a
    # standard phrase or its expansion. Do not silently resolve disagreements.
    occupied = {}
    exp = catalog["expansion"]
    for command in catalog["commands"]:
        key = command["command_key"]
        for phrase in command["phrases"]:
            profile = exp["phrase_profiles"].get(phrase, exp["command_profiles"].get(key, exp["default_profile"]))
            forms = [phrase] + [r["template"].format(phrase=phrase) for r in exp["profiles"][profile]]
            for form in forms:
                normalized = normalize_command_phrase(form)
                if normalized in occupied:
                    raise ValueError(f"Standard/expansion collision: {form}")
                occupied[normalized] = key
    for key, variants in rules["phrase_variants"].items():
        result = []
        for phrase in variants:
            normalized = normalize_command_phrase(phrase)
            if normalized in occupied:
                if occupied[normalized] != key:
                    raise ValueError(f"Alias collision: {phrase}")
                continue
            occupied[normalized] = key
            result.append(phrase)
        if result:
            catalog["phrase_variants"][key] = result
    for row, route in rules["non_catalog_rows"].items():
        cells = rows[row]
        catalog["non_catalog_entries"].append({
            "source_row": row, "route": route, "recognition": cells["D"],
            "behavior": cells.get("E", ""), "secondary_behavior": cells.get("F", ""),
        })
    return catalog, rules


def render(catalog: dict, rules: dict) -> dict[Path, str]:
    counts = (len(catalog["commands"]), sum(len(c["phrases"]) for c in catalog["commands"]),
              sum(len(v) for v in catalog["phrase_variants"].values()))
    commands, phrases, variants = counts
    extra = sum(1 for c in catalog["commands"] if not c["source_rows"])
    matrix = ["# 确定性词库测试对齐表", "", "由 `scripts/generate_command_catalog.py` 生成。修改 XLSX 和审核规则后重新生成。", "",
              f"版本 `{catalog['version']}`，来源 [{catalog['source_name']}](语音识别内容.xlsx) 的 Sheet1。",
              f"共 {commands + len(catalog['non_catalog_entries'])} 个功能条目：{commands} 个固定路由（含 ASR 呼名"
              + (f"，{extra} 个是 XLSX 外的新需求表口令，Excel 行记「新表」" if extra else "") + "），4 个参数句式、1个闲聊兜底和1个声源入口。",
              f"标准词句 {phrases} 条，受控整句扩展 {phrases * 10} 条，复用口语/ASR变体 {variants} 条，共 {phrases * 11 + variants} 个精确入口。",
              "`source_rows` 是 Excel 实际行号。叫两声/说话优先 BARK；去/走开/翻滚等旧独立路由已移出本版。", "",
              "每句话只发布一个具体事件；PRAISE/SCOLD 为 social_reaction（可执行=false，BT=true）；CALL_NAME 两项均 false；其余固定路由两项均 true。",
              "BT=true 表示 Voice 允许下游接收，不证明 Tree/Action 已实现或真机已执行。硬件唤醒仍由模块配置，ASR 呼名不代替唤醒。", "",
              "| Excel行 | 命令 | 标准词句 | 事件 / 编号 |", "|---|---|---|---|"]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["source_rows", "command_key", "command_id", "event_type", "standard_phrases", "variants", "behavior", "secondary_behavior", "notes", "is_executable", "should_trigger_behavior_tree", "dispatch_role"])
    kws = ["# 由 generate_command_catalog.py 生成；短动作子集，完整长句由 ASR 词库确认。"]
    for c in catalog["commands"]:
        row = c["source_rows"][0] if c["source_rows"] else "新表"
        matrix.append(f"| {row} | {c['command_key']} | {'、'.join(c['phrases'])} | `{c['event_type']}` / `{c['command_id']}` |")
        social = c.get("emotion") in {"PRAISE", "SCOLD"}
        executable = c["control"] == "DO" and c.get("emotion", "NONE") == "NONE"
        writer.writerow([row, c["command_key"], c["command_id"], c["event_type"], " | ".join(c["phrases"]),
                         " | ".join(catalog["phrase_variants"].get(c["command_key"], [])), c["behavior"], c["secondary_behavior"], c["notes"],
                         str(executable).lower(), str(executable or social).lower(),
                         "call" if c["command_key"] == "CALL_NAME" else "social_reaction" if social else "specific_command"])
        if c["command_key"] in rules["kws_command_keys"]:
            for phrase in c["phrases"]:
                # Long/social/parameter utterances stay on the whole-ASR path.
                if 2 <= len(phrase) <= 5 and phrase.isascii() is False:
                    kws.append(f"{phrase} @{c['command_key']}")
    matrix += ["", "## 参数、兜底及声源入口", "", "| Excel行 | 路由 | 识别内容 |", "|---|---|---|"]
    for entry in catalog["non_catalog_entries"]:
        matrix.append(f"| {entry['source_row']} | {entry['route']} | {entry['recognition'].replace(chr(10), '<br>')} |")
        key = entry["route"]
        sound = key == "SOUND_SOURCE"
        chat = key == "CHAT_FALLBACK"
        command_id = ("" if sound else "CMD_UNKNOWN" if chat
                      else "CMD_FETCH_OBJECT" if key == "FETCH" else "CMD_" + key)
        event_type = ("EVT_VOICE_SOUND_SOURCE_DETECTED" if sound else "EVT_VOICE_COMMAND_UNKNOWN" if chat
                      else "EVT_VOICE_COMMAND_" + key)
        note = ("有效 CDC vad/update 与 DOA 触发通用声音活动事件；不区分拍手等声音类别；动作序列由下游实现" if sound
                else "未匹配文本发布 UNKNOWN，不触发闲聊动作池" if chat
                else "参数行标志代表目标已支持分支；未知目标两个标志均 false")
        writer.writerow([entry["source_row"], key, command_id, event_type,
                         entry["recognition"], "", entry["behavior"], entry["secondary_behavior"], note,
                         str(not (sound or chat)).lower(), str(not chat).lower(),
                         "sound_source" if sound else "diagnostic" if chat else "specific_command"])
    matrix += ["", "## 验收边界", "", "- 逐条验证标准词句及变体的整句事件；同一句不能同时触发特技与问候。",
               "- 否定、引用、第三人陈述及多目标不得靠截取子串执行；明确的停止游戏、责备与制止语句允许整句命中。",
               "- 去厨房/门口拿狗绳子须同时保留地点与物品；泛称玩具通过 object_group=toy 授权下游选择目标，未注册人物、未知地点/物品仍为不可执行摘要。",
               "- 去追球使用 CHASE_BALL 独立事件，与追人 CHASE 和取物 FETCH 分开；下游需接入追球及随机放球流程和5分钟任务上限。",
               "- 声源行仅对齐通用声音活动入口，不证明拍手、嘬声或异响分类已实现；闲聊仍为非执行 UNKNOWN。",
               "- 观察同一 utterance_id 的 command_lexicon、recognition_arbitration、event_publish；词库加载成功不等于真机验收。", "",
               "## 表格全局约束（下游实现）", "", catalog["global_constraints"], ""]
    return {
        ROOT / "config/command_catalog.yaml": "# 由 XLSX 和 command_catalog_rules.yaml 生成，请勿直接编辑。\n" + yaml.safe_dump(catalog, allow_unicode=True, sort_keys=False, width=100),
        ROOT / "config/kws_keywords_raw.txt": "\n".join(kws) + "\n",
        ROOT / "docs/COMMAND_CATALOG_TEST_MATRIX.md": "\n".join(matrix),
        ROOT / "docs/DOWNSTREAM_COMMAND_MAP.csv": "\ufeff" + output.getvalue(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    catalog, rules = build()
    stale = []
    for path, content in render(catalog, rules).items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content.replace("\r\n", "\n"):
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.write_text(content, encoding="utf-8")
    print("Stale: " + ", ".join(stale) if stale else f"OK: {catalog['version']}")
    return int(bool(stale))


if __name__ == "__main__":
    raise SystemExit(main())
