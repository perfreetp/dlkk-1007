#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
玩具仓库盘点命令行工具 - 适用于幼儿园或玩具租赁仓
"""

import argparse
import json
import csv
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path


BASE_DIR = Path.cwd()
CONFIG_FILE = BASE_DIR / "config.json"
INVENTORY_FILE = BASE_DIR / "inventory.json"
LOGS_FILE = BASE_DIR / "logs.json"


# ==================== 数据存储辅助函数 ====================

def load_json(filepath, default):
    if filepath.exists():
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return default
    return default


def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_config():
    return load_json(CONFIG_FILE, {
        "locations": {},
        "safety_stock": {},
        "overdue_days": 30,
        "operator": "system",
        "initialized": False
    })


def save_config(cfg):
    save_json(CONFIG_FILE, cfg)


def get_inventory():
    return load_json(INVENTORY_FILE, {"items": {}})


def save_inventory(inv):
    save_json(INVENTORY_FILE, inv)


def get_logs():
    return load_json(LOGS_FILE, {"records": []})


def save_logs(logs):
    save_json(LOGS_FILE, logs)


def log_action(action, detail, operator=None):
    cfg = get_config()
    logs = get_logs()
    logs["records"].append({
        "id": str(uuid.uuid4())[:8],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "operator": operator or cfg.get("operator", "system"),
        "action": action,
        "detail": detail
    })
    save_logs(logs)


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def print_table(headers, rows):
    if not rows:
        print("(无数据)")
        return
    col_widths = [len(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))
    separator = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    header_line = "|" + "|".join(
        f" {str(h).ljust(col_widths[i])} " for i, h in enumerate(headers)
    ) + "|"
    print(separator)
    print(header_line)
    print(separator)
    for row in rows:
        line = "|" + "|".join(
            f" {str(cell).ljust(col_widths[i])} " for i, cell in enumerate(row)
        ) + "|"
        print(line)
    print(separator)


# ==================== init 命令 ====================

def cmd_init(args):
    """初始化库位结构"""
    cfg = get_config()
    if cfg.get("initialized") and not args.force:
        print("⚠ 仓库已初始化。如需重新初始化请使用 --force 参数")
        return

    print("=== 玩具仓库库位初始化 ===")
    
    rows = args.rows if args.rows else 3
    cols = args.cols if args.cols else 4
    shelves_per_loc = args.shelves if args.shelves else 2

    locations = {}
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            loc_code = f"A{r:02d}-{c:02d}"
            locations[loc_code] = {
                "code": loc_code,
                "description": f"{chr(64+r)}区第{c}列",
                "shelves": [f"{loc_code}-S{i}" for i in range(1, shelves_per_loc + 1)],
                "capacity": args.capacity or 50,
                "created_at": now_str()
            }
    
    cfg["locations"] = locations
    cfg["initialized"] = True
    if args.overdue_days:
        cfg["overdue_days"] = args.overdue_days
    save_config(cfg)
    log_action("INIT", f"初始化库位: {len(locations)}个区域, 每区{shelves_per_loc}层货架, 共{rows}行{cols}列")
    
    print(f"\n✅ 初始化成功！共创建 {len(locations)} 个库位区域：")
    headers = ["库位编号", "描述", "货架列表", "容量"]
    table_rows = []
    for code, loc in locations.items():
        table_rows.append([code, loc["description"], ", ".join(loc["shelves"]), loc["capacity"]])
    print_table(headers, table_rows)


# ==================== import 命令 ====================

def cmd_import(args):
    """导入玩具台账（支持CSV/JSON）"""
    cfg = get_config()
    if not cfg.get("initialized"):
        print("❌ 仓库尚未初始化，请先运行 init 命令")
        return

    inv = get_inventory()
    filepath = Path(args.file)
    if not filepath.exists():
        print(f"❌ 文件不存在: {filepath}")
        return

    imported = 0
    skipped = 0
    items = inv["items"]

    if filepath.suffix.lower() == ".json":
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            toy_list = data if isinstance(data, list) else data.get("items", [])
        except Exception as e:
            print(f"❌ 读取JSON失败: {e}")
            return
    else:
        toy_list = []
        try:
            with open(filepath, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    toy_list.append({k.strip(): v.strip() for k, v in row.items()})
        except Exception as e:
            print(f"❌ 读取CSV失败: {e}")
            return

    default_location = args.default_location
    locations = list(cfg["locations"].keys())

    for toy in toy_list:
        code = toy.get("编号") or toy.get("code") or toy.get("id")
        if not code:
            skipped += 1
            continue
        
        if code in items and not args.update:
            skipped += 1
            continue

        location = toy.get("库位") or toy.get("location") or default_location
        if not location:
            location = locations[0] if locations else "A01-01"
        
        shelf = toy.get("货架") or toy.get("shelf")
        if location in cfg["locations"] and not shelf:
            shelf = cfg["locations"][location]["shelves"][0]

        qty_raw = toy.get("数量") or toy.get("quantity") or toy.get("qty") or "1"
        try:
            quantity = int(qty_raw)
        except (ValueError, TypeError):
            quantity = 1

        items[code] = {
            "code": code,
            "name": toy.get("名称") or toy.get("name") or f"玩具{code}",
            "category": toy.get("分类") or toy.get("category") or "未分类",
            "brand": toy.get("品牌") or toy.get("brand") or "",
            "age_range": toy.get("适用年龄") or toy.get("age") or "",
            "location": location,
            "shelf": shelf or "",
            "quantity": quantity,
            "price": float(toy.get("单价") or toy.get("price") or 0),
            "condition": "完好",
            "status": "在库",
            "borrower": "",
            "borrow_date": "",
            "due_date": "",
            "inbound_date": toy.get("入库日期") or toy.get("inbound_date") or now_str()[:10],
            "last_scan": "",
            "scan_count": 0,
            "remarks": toy.get("备注") or toy.get("remarks") or ""
        }
        imported += 1

    inv["items"] = items
    save_inventory(inv)
    log_action("IMPORT", f"导入玩具台账: 成功{imported}条, 跳过{skipped}条, 来源文件: {filepath.name}")
    
    print(f"\n✅ 导入完成！成功 {imported} 条，跳过 {skipped} 条")
    if imported > 0:
        print("\n前5条记录预览：")
        headers = ["编号", "名称", "分类", "库位", "货架", "数量"]
        rows = []
        for i, (code, item) in enumerate(list(items.items())[-imported:]):
            if i >= 5:
                break
            rows.append([code, item["name"], item["category"], item["location"], item["shelf"], item["quantity"]])
        print_table(headers, rows)


# ==================== scan 命令 ====================

def cmd_scan(args):
    """扫码操作：入库/借出/归还/报废"""
    cfg = get_config()
    inv = get_inventory()
    items = inv["items"]

    op = args.operation
    operator = args.operator or cfg.get("operator", "system")
    codes = args.codes if args.codes else []

    if not codes:
        print("请输入玩具编号（一行一个，输入空行结束）：")
        while True:
            try:
                line = input("> ").strip()
                if not line:
                    break
                codes.append(line)
            except EOFError:
                break

    if not codes:
        print("❌ 未输入任何编号")
        return

    success = []
    failed = []
    details_list = []

    for code in codes:
        if op == "inbound":
            loc = args.location
            if not loc:
                locs = list(cfg["locations"].keys())
                loc = locs[0] if locs else "A01-01"
            if loc not in cfg["locations"]:
                failed.append((code, f"库位{loc}不存在"))
                continue
            shelf = args.shelf or cfg["locations"][loc]["shelves"][0]
            
            if code in items:
                items[code]["quantity"] += args.qty
                items[code]["location"] = loc
                items[code]["shelf"] = shelf
                items[code]["status"] = "在库"
                items[code]["condition"] = "完好"
            else:
                name = args.name or f"玩具{code}"
                items[code] = {
                    "code": code,
                    "name": name,
                    "category": args.category or "未分类",
                    "brand": args.brand or "",
                    "age_range": args.age or "",
                    "location": loc,
                    "shelf": shelf,
                    "quantity": args.qty,
                    "price": args.price or 0,
                    "condition": "完好",
                    "status": "在库",
                    "borrower": "",
                    "borrow_date": "",
                    "due_date": "",
                    "inbound_date": now_str()[:10],
                    "last_scan": now_str(),
                    "scan_count": 1,
                    "remarks": ""
                }
            items[code]["last_scan"] = now_str()
            items[code]["scan_count"] += 1
            success.append(code)
            details_list.append(f"{code}入库: 库位{loc}/{shelf}, 数量{args.qty}")

        elif op == "borrow":
            if code not in items:
                failed.append((code, "编号不存在"))
                continue
            if items[code]["status"] == "借出":
                failed.append((code, f"已被{items[code]['borrower']}借出"))
                continue
            if items[code]["quantity"] <= 0:
                failed.append((code, "库存不足"))
                continue
            if not args.borrower:
                failed.append((code, "未指定借出人(-b)"))
                continue
            items[code]["status"] = "借出"
            items[code]["borrower"] = args.borrower
            items[code]["borrow_date"] = now_str()[:10]
            overdue = cfg.get("overdue_days", 30)
            due = (datetime.now() + timedelta(days=overdue)).strftime("%Y-%m-%d")
            items[code]["due_date"] = args.due or due
            items[code]["quantity"] -= args.qty
            items[code]["last_scan"] = now_str()
            items[code]["scan_count"] += 1
            success.append(code)
            details_list.append(f"{code}借出: {args.borrower}, 到期{items[code]['due_date']}")

        elif op == "return":
            if code not in items:
                failed.append((code, "编号不存在"))
                continue
            if items[code]["status"] != "借出":
                failed.append((code, f"当前状态为{items[code]['status']}"))
                continue
            condition = args.condition or "完好"
            items[code]["status"] = "在库"
            items[code]["quantity"] += args.qty
            items[code]["condition"] = condition
            items[code]["last_scan"] = now_str()
            items[code]["scan_count"] += 1
            if condition != "完好":
                items[code]["status"] = "待维修"
            success.append(code)
            details_list.append(f"{code}归还: 状态{condition}")

        elif op == "scrap":
            if code not in items:
                failed.append((code, "编号不存在"))
                continue
            reason = args.reason or "无原因"
            items[code]["status"] = "报废"
            items[code]["condition"] = "破损"
            items[code]["quantity"] = 0
            items[code]["last_scan"] = now_str()
            items[code]["scan_count"] += 1
            items[code]["remarks"] = f"报废原因: {reason}"
            success.append(code)
            details_list.append(f"{code}报废: {reason}")

    save_inventory(inv)
    log_action(f"SCAN-{op.upper()}", f"操作人:{operator}; 成功:{len(success)}条{', '.join(success)}; 失败:{len(failed)}条; 详情:{'; '.join(details_list)}", operator)

    print(f"\n📊 操作结果【{op}】操作人: {operator}")
    print(f"  成功: {len(success)} 件 {success[:10]}{'...' if len(success) > 10 else ''}")
    if failed:
        print(f"  失败: {len(failed)} 件")
        for c, r in failed:
            print(f"    - {c}: {r}")


# ==================== move 命令 ====================

def cmd_move(args):
    """库位调拨"""
    cfg = get_config()
    inv = get_inventory()
    items = inv["items"]
    operator = args.operator or cfg.get("operator", "system")

    if args.all_from:
        if args.all_from not in cfg["locations"]:
            print(f"❌ 源库位 {args.all_from} 不存在")
            return
        if args.to not in cfg["locations"]:
            print(f"❌ 目标库位 {args.to} 不存在")
            return
        target_shelf = args.shelf or cfg["locations"][args.to]["shelves"][0]
        moved = 0
        for code, item in items.items():
            if item["location"] == args.all_from and item["status"] == "在库":
                old = f"{item['location']}/{item['shelf']}"
                item["location"] = args.to
                item["shelf"] = target_shelf
                moved += 1
        save_inventory(inv)
        log_action("MOVE-ALL", f"整库调拨: {args.all_from} -> {args.to}/{target_shelf}, 共{moved}件", operator)
        print(f"✅ 整库调拨完成：从 {args.all_from} 调拨 {moved} 件到 {args.to}/{target_shelf}")
        return

    if not args.codes:
        print("❌ 请指定要调拨的玩具编号 (--codes) 或整库调拨 (--all-from)")
        return
    if not args.to:
        print("❌ 请指定目标库位 (--to)")
        return
    if args.to not in cfg["locations"]:
        print(f"❌ 目标库位 {args.to} 不存在")
        return

    target_shelf = args.shelf or cfg["locations"][args.to]["shelves"][0]
    success = []
    failed = []

    for code in args.codes:
        if code not in items:
            failed.append((code, "编号不存在"))
            continue
        old_loc = f"{items[code]['location']}/{items[code]['shelf']}"
        items[code]["location"] = args.to
        items[code]["shelf"] = target_shelf
        items[code]["last_scan"] = now_str()
        success.append((code, old_loc))

    save_inventory(inv)
    details = "; ".join(f"{c}: {o} -> {args.to}/{target_shelf}" for c, o in success)
    log_action("MOVE", f"操作人:{operator}; 成功{len(success)}件; {details}", operator)

    print(f"\n📦 库位调拨结果  操作人: {operator}")
    print(f"  成功: {len(success)} 件")
    headers = ["编号", "原库位", "新库位"]
    rows = [[c, o, f"{args.to}/{target_shelf}"] for c, o in success]
    print_table(headers, rows)
    if failed:
        print(f"  失败: {len(failed)} 件")
        for c, r in failed:
            print(f"    - {c}: {r}")


# ==================== check 命令 ====================

def cmd_check(args):
    """按箱盘点 / 逾期提醒"""
    cfg = get_config()
    inv = get_inventory()
    items = inv["items"]

    if args.overdue:
        overdue_days = cfg.get("overdue_days", 30)
        today = datetime.now().date()
        overdue_items = []
        for code, item in items.items():
            if item["status"] == "借出" and item["due_date"]:
                try:
                    due = datetime.strptime(item["due_date"], "%Y-%m-%d").date()
                    if today > due:
                        days_overdue = (today - due).days
                        overdue_items.append([code, item["name"], item["borrower"], item["borrow_date"], item["due_date"], f"{days_overdue}天"])
                except ValueError:
                    continue
        print(f"\n⚠ 逾期未还清单 (超过{overdue_days}天视为逾期)")
        headers = ["编号", "名称", "借出人", "借出日期", "应还日期", "逾期天数"]
        print_table(headers, overdue_items)
        print(f"合计: {len(overdue_items)} 件逾期")
        return

    if args.all:
        print("\n📋 全面盘点 - 所有在库物品")
        total = 0
        headers = ["编号", "名称", "分类", "库位", "货架", "数量", "状态", "状况"]
        rows = []
        for code, item in items.items():
            rows.append([code, item["name"], item["category"], item["location"], item["shelf"], item["quantity"], item["status"], item["condition"]])
            total += item["quantity"]
        print_table(headers, rows)
        print(f"\n合计: {len(rows)} 个品种, 总数量 {total} 件")
        return

    if args.location:
        loc = args.location
        if loc not in cfg["locations"]:
            print(f"❌ 库位 {loc} 不存在")
            return
        loc_items = []
        loc_qty = 0
        headers = ["编号", "名称", "分类", "货架", "数量", "状态"]
        for code, item in items.items():
            if item["location"] == loc:
                loc_items.append([code, item["name"], item["category"], item["shelf"], item["quantity"], item["status"]])
                loc_qty += item["quantity"]
        print(f"\n📦 按库位盘点: {loc} - {cfg['locations'][loc]['description']}")
        print(f"   货架: {', '.join(cfg['locations'][loc]['shelves'])}  容量: {cfg['locations'][loc]['capacity']}")
        print_table(headers, loc_items)
        print(f"合计: {len(loc_items)} 个品种, 数量 {loc_qty} 件")
        return

    if args.borrowed:
        borrowed = []
        for code, item in items.items():
            if item["status"] == "借出":
                borrowed.append([code, item["name"], item["category"], item["borrower"], item["borrow_date"], item["due_date"]])
        print("\n📤 借出中物品清单")
        headers = ["编号", "名称", "分类", "借出人", "借出日期", "应还日期"]
        print_table(headers, borrowed)
        print(f"合计: {len(borrowed)} 件借出中")
        return

    print("请指定盘点方式: --location 库位 / --all 全部 / --borrowed 借出 / --overdue 逾期")


# ==================== report 命令 ====================

def cmd_report(args):
    """生成差异清单 / 库龄分析 / 分类统计"""
    inv = get_inventory()
    items = inv["items"]

    if args.type == "diff":
        expected_codes = set()
        if args.file:
            fp = Path(args.file)
            if fp.exists():
                with open(fp, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        for k, v in row.items():
                            if "编号" in k or "code" in k.lower():
                                if v.strip():
                                    expected_codes.add(v.strip())
        
        actual_codes = set(items.keys())
        extra = actual_codes - expected_codes
        missing = expected_codes - actual_codes
        
        print("\n🔍 盘点差异清单")
        print(f"  台账应有: {len(expected_codes)} 个品种")
        print(f"  实际盘点: {len(actual_codes)} 个品种")
        
        if missing:
            print(f"\n❌ 缺少 (台账有但库中无): {len(missing)} 件")
            headers = ["编号", "台账状态"]
            rows = [[c, "缺失"] for c in sorted(missing)]
            print_table(headers, rows)
        
        if extra:
            print(f"\n⚠ 多余 (库中有但台账无): {len(extra)} 件")
            headers = ["编号", "名称", "库位", "数量"]
            rows = [[c, items[c]["name"], items[c]["location"], items[c]["quantity"]] for c in sorted(extra)]
            print_table(headers, rows)
        
        if not missing and not extra and expected_codes:
            print("\n✅ 账实相符，无差异！")
        return

    if args.type == "age":
        today = datetime.now().date()
        age_buckets = {"0-30天": [], "31-90天": [], "91-180天": [], "181-365天": [], "365天以上": []}
        for code, item in items.items():
            if item["inbound_date"] and item["status"] == "在库":
                try:
                    in_date = datetime.strptime(item["inbound_date"], "%Y-%m-%d").date()
                    days = (today - in_date).days
                    entry = [code, item["name"], item["category"], item["inbound_date"], f"{days}天", item["quantity"]]
                    if days <= 30:
                        age_buckets["0-30天"].append(entry)
                    elif days <= 90:
                        age_buckets["31-90天"].append(entry)
                    elif days <= 180:
                        age_buckets["91-180天"].append(entry)
                    elif days <= 365:
                        age_buckets["181-365天"].append(entry)
                    else:
                        age_buckets["365天以上"].append(entry)
                except ValueError:
                    continue
        
        print("\n📊 库龄分析报告")
        headers = ["库龄区间", "品种数", "总数量"]
        summary_rows = []
        for bucket in ["0-30天", "31-90天", "91-180天", "181-365天", "365天以上"]:
            entries = age_buckets[bucket]
            total_qty = sum(e[5] for e in entries)
            summary_rows.append([bucket, len(entries), total_qty])
        print_table(headers, summary_rows)

        if args.detail:
            for bucket in ["365天以上", "181-365天"]:
                if age_buckets[bucket]:
                    print(f"\n📌 {bucket} 明细 (呆滞库存建议关注):")
                    print_table(["编号", "名称", "分类", "入库日期", "库龄", "数量"], age_buckets[bucket][:args.detail])
        return

    if args.type == "category":
        cat_stats = {}
        for code, item in items.items():
            cat = item["category"]
            if cat not in cat_stats:
                cat_stats[cat] = {"count": 0, "qty": 0, "value": 0, "on_shelf": 0, "borrowed": 0, "scrapped": 0}
            cat_stats[cat]["count"] += 1
            cat_stats[cat]["qty"] += item["quantity"]
            cat_stats[cat]["value"] += item["quantity"] * item["price"]
            if item["status"] == "在库":
                cat_stats[cat]["on_shelf"] += item["quantity"]
            elif item["status"] == "借出":
                cat_stats[cat]["borrowed"] += item["quantity"]
            elif item["status"] == "报废":
                cat_stats[cat]["scrapped"] += item["quantity"]
        
        print("\n📊 分类统计报告")
        headers = ["分类", "品种数", "总数量", "在库", "借出", "报废", "库存金额"]
        rows = []
        total = {"count": 0, "qty": 0, "value": 0, "on_shelf": 0, "borrowed": 0, "scrapped": 0}
        for cat in sorted(cat_stats.keys()):
            s = cat_stats[cat]
            rows.append([cat, s["count"], s["qty"], s["on_shelf"], s["borrowed"], s["scrapped"], f"¥{s['value']:.2f}"])
            for k in total:
                total[k] += s[k]
        rows.append(["合计", total["count"], total["qty"], total["on_shelf"], total["borrowed"], total["scrapped"], f"¥{total['value']:.2f}"])
        print_table(headers, rows)

        cfg = get_config()
        ss = cfg.get("safety_stock", {})
        low_stock = []
        for cat in cat_stats:
            threshold = ss.get(cat, ss.get("_default", 0))
            if threshold and cat_stats[cat]["on_shelf"] < threshold:
                low_stock.append([cat, cat_stats[cat]["on_shelf"], threshold, "⚠ 低于安全库存"])
        if low_stock:
            print("\n🚨 安全库存预警:")
            print_table(["分类", "当前在库", "安全阈值", "状态"], low_stock)
        return

    if args.type == "logs":
        logs = get_logs()
        records = logs["records"]
        if args.limit:
            records = records[-args.limit:]
        print(f"\n📝 操作日志 (最近 {len(records)} 条)")
        headers = ["时间", "操作人", "操作", "详情"]
        rows = [[r["timestamp"], r["operator"], r["action"], (r["detail"][:50] + "...") if len(r["detail"]) > 50 else r["detail"]] for r in records]
        print_table(headers, rows)
        return

    print("请指定报告类型: diff / age / category / logs")


# ==================== label 命令 ====================

def cmd_label(args):
    """打印标签 / 设置安全库存"""
    cfg = get_config()
    inv = get_inventory()
    items = inv["items"]

    if args.safety:
        category = args.category or "_default"
        threshold = args.threshold or 0
        if "safety_stock" not in cfg:
            cfg["safety_stock"] = {}
        if args.list:
            print("\n🛡 当前安全库存设置:")
            headers = ["分类", "安全阈值"]
            rows = [[k, v] for k, v in cfg["safety_stock"].items()]
            print_table(headers, rows)
            return
        cfg["safety_stock"][category] = threshold
        save_config(cfg)
        log_action("SAFETY-STOCK", f"设置 {category} 安全库存阈值为 {threshold}")
        print(f"✅ 已设置分类【{category}】安全库存阈值为 {threshold}")
        return

    if args.operator:
        cfg["operator"] = args.operator
        save_config(cfg)
        print(f"✅ 已设置默认操作人为: {args.operator}")
        return

    if args.overdue:
        cfg["overdue_days"] = args.overdue
        save_config(cfg)
        print(f"✅ 已设置逾期阈值为: {args.overdue} 天")
        return

    codes = args.codes if args.codes else []
    if not codes and not args.all:
        print("❌ 请指定打印标签的编号 (--codes) 或 --all")
        return
    
    if args.all:
        codes = list(items.keys())
    
    labels = []
    for code in codes:
        if code in items:
            it = items[code]
            labels.append({
                "code": it["code"],
                "name": it["name"],
                "category": it["category"],
                "location": f"{it['location']}/{it['shelf']}",
                "price": it["price"]
            })
        else:
            labels.append({"code": code, "name": "未登记", "category": "-", "location": "-", "price": 0})

    labels_text = ""
    per_row = args.cols or 2
    width = 38
    
    for i, lab in enumerate(labels):
        border = "+" + "-" * (width - 2) + "+"
        line1 = f"| 【{lab['code']}】" + " " * (width - len(lab['code']) - 8) + "|"
        line2 = f"| 名称: {lab['name'][:20]}" + " " * max(0, width - len(lab['name']) - 10) + "|"
        line3 = f"| 分类: {lab['category'][:18]}" + " " * max(0, width - len(lab['category']) - 8) + "|"
        line4 = f"| 库位: {lab['location'][:18]}" + " " * max(0, width - len(lab['location']) - 8) + "|"
        line5 = f"| 价格: ¥{lab['price']:.2f}" + " " * (width - 15) + "|"
        block = "\n".join([border, line1, line2, line3, line4, line5, border])
        labels_text += block + "\n"
        
        if (i + 1) % per_row == 0:
            labels_text += "\n"

    output_file = args.output or "labels.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(labels_text)
    
    log_action("LABEL", f"生成标签 {len(labels)} 张 -> {output_file}")
    print(f"🏷 已生成 {len(labels)} 张标签，保存至: {output_file}")
    print("\n标签预览 (前3张):")
    preview_lines = labels_text.split("\n")[:7 * min(3, per_row) * 2]
    print("\n".join(preview_lines))


# ==================== export 命令 ====================

def cmd_export(args):
    """导出盘点表给负责人"""
    cfg = get_config()
    inv = get_inventory()
    items = inv["items"]
    logs = get_logs()

    fmt = args.format.lower()
    output = args.output or f"inventory_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    if fmt == "csv" or fmt == "all":
        csv_file = output + ".csv"
        with open(csv_file, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["编号", "名称", "分类", "品牌", "适用年龄", "库位", "货架",
                            "数量", "单价", "状态", "状况", "借出人", "借出日期", "应还日期",
                            "入库日期", "库龄(天)", "备注"])
            today = datetime.now().date()
            for code, it in sorted(items.items()):
                age_days = ""
                if it["inbound_date"]:
                    try:
                        age_days = (today - datetime.strptime(it["inbound_date"], "%Y-%m-%d").date()).days
                    except ValueError:
                        pass
                writer.writerow([
                    it["code"], it["name"], it["category"], it["brand"], it["age_range"],
                    it["location"], it["shelf"], it["quantity"], it["price"],
                    it["status"], it["condition"], it["borrower"], it["borrow_date"],
                    it["due_date"], it["inbound_date"], age_days, it["remarks"]
                ])
        print(f"📄 CSV盘点表已导出: {csv_file}")

    if fmt == "json" or fmt == "all":
        json_file = output + ".json"
        report = {
            "generated_at": now_str(),
            "summary": {
                "total_types": len(items),
                "total_qty": sum(it["quantity"] for it in items.values()),
                "on_shelf_qty": sum(it["quantity"] for it in items.values() if it["status"] == "在库"),
                "borrowed_qty": sum(1 for it in items.values() if it["status"] == "借出"),
                "scrapped_qty": sum(1 for it in items.values() if it["status"] == "报废"),
                "total_locations": len(cfg["locations"]),
                "total_ops": len(logs["records"])
            },
            "items": list(items.values()),
            "locations": cfg["locations"],
            "safety_stock": cfg.get("safety_stock", {}),
            "safety_alerts": []
        }
        
        cat_stats = {}
        for it in items.values():
            cat = it["category"]
            if cat not in cat_stats:
                cat_stats[cat] = 0
            if it["status"] == "在库":
                cat_stats[cat] += it["quantity"]
        ss = cfg.get("safety_stock", {})
        for cat, qty in cat_stats.items():
            threshold = ss.get(cat, ss.get("_default", 0))
            if threshold and qty < threshold:
                report["safety_alerts"].append({"category": cat, "current": qty, "threshold": threshold})
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"📄 JSON完整报告已导出: {json_file}")

    if fmt == "txt" or fmt == "all":
        txt_file = output + ".txt"
        today = datetime.now()
        total_types = len(items)
        total_qty = sum(it["quantity"] for it in items.values())
        on_shelf = sum(it["quantity"] for it in items.values() if it["status"] == "在库")
        borrowed = sum(1 for it in items.values() if it["status"] == "借出")
        scrapped = sum(1 for it in items.values() if it["status"] == "报废")
        to_repair = sum(1 for it in items.values() if it["status"] == "待维修")
        
        cat_stats = {}
        for it in items.values():
            cat = it["category"]
            if cat not in cat_stats:
                cat_stats[cat] = {"types": 0, "qty": 0}
            cat_stats[cat]["types"] += 1
            cat_stats[cat]["qty"] += it["quantity"]
        
        overdue_list = []
        today_d = today.date()
        for code, it in items.items():
            if it["status"] == "借出" and it["due_date"]:
                try:
                    due = datetime.strptime(it["due_date"], "%Y-%m-%d").date()
                    if today_d > due:
                        overdue_list.append((code, it["name"], it["borrower"], (today_d - due).days))
                except ValueError:
                    pass

        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write("         玩具仓库盘点报告\n")
            f.write(f"    生成时间: {now_str()}\n")
            f.write(f"    操作人:   {cfg.get('operator', 'system')}\n")
            f.write("=" * 60 + "\n\n")
            
            f.write("【一、总体概况】\n")
            f.write(f"  品种总数: {total_types}\n")
            f.write(f"  数量合计: {total_qty}\n")
            f.write(f"  在库数量: {on_shelf}\n")
            f.write(f"  借出数量: {borrowed}\n")
            f.write(f"  待维修:   {to_repair}\n")
            f.write(f"  已报废:   {scrapped}\n")
            f.write(f"  库位总数: {len(cfg['locations'])}\n")
            f.write(f"  累计操作: {len(logs['records'])} 次\n\n")
            
            f.write("【二、分类统计】\n")
            for cat in sorted(cat_stats.keys()):
                s = cat_stats[cat]
                f.write(f"  {cat}: {s['types']}个品种, {s['qty']}件\n")
            f.write("\n")
            
            if overdue_list:
                f.write("【三、逾期未还预警】\n")
                for code, name, borrower, days in overdue_list:
                    f.write(f"  ⚠ [{code}] {name} - {borrower} 逾期{days}天\n")
                f.write("\n")
            
            f.write("【四、库位明细盘点】\n")
            for loc_code in sorted(cfg["locations"].keys()):
                loc = cfg["locations"][loc_code]
                loc_items = [(c, it) for c, it in items.items() if it["location"] == loc_code]
                loc_qty = sum(it["quantity"] for _, it in loc_items)
                f.write(f"\n  ▶ {loc_code} ({loc['description']}) - 共{len(loc_items)}个品种, {loc_qty}件\n")
                for code, it in loc_items:
                    f.write(f"      {code} | {it['name']} | {it['shelf']} | ×{it['quantity']} | {it['status']}\n")
            
            f.write("\n" + "=" * 60 + "\n")
            f.write("              报告结束\n")
            f.write("=" * 60 + "\n")
        
        print(f"📄 TXT盘点报告已导出: {txt_file}")
    
    log_action("EXPORT", f"导出盘点报告, 格式={fmt}, 输出前缀={output}")
    print(f"\n✅ 导出完成！请将报告发送给负责人审阅。")


# ==================== 主入口 ====================

def build_parser():
    parser = argparse.ArgumentParser(
        prog="toy-inventory",
        description="🏫 玩具仓库盘点命令行工具 - 幼儿园/玩具租赁仓专用",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
命令速查:
  init     初始化仓库库位结构
  import   导入玩具台账 (CSV/JSON)
  scan     扫码登记: 入库/借出/归还/报废
  move     库位调拨 (单件/整库)
  check    按箱盘点 / 逾期提醒
  report   差异清单 / 库龄 / 分类统计 / 日志
  label    打印标签 / 安全库存设置
  export   导出盘点表 (CSV/JSON/TXT)
        """
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="可用命令")

    # init
    p_init = subparsers.add_parser("init", help="初始化仓库库位")
    p_init.add_argument("--rows", type=int, help="库位区域行数")
    p_init.add_argument("--cols", type=int, help="库位区域列数")
    p_init.add_argument("--shelves", type=int, help="每区货架层数")
    p_init.add_argument("--capacity", type=int, help="每区容量")
    p_init.add_argument("--overdue-days", type=int, help="逾期阈值天数")
    p_init.add_argument("--force", action="store_true", help="强制重新初始化")
    p_init.set_defaults(func=cmd_init)

    # import
    p_imp = subparsers.add_parser("import", help="导入玩具台账")
    p_imp.add_argument("file", help="CSV 或 JSON 文件路径")
    p_imp.add_argument("--default-location", help="默认库位编号")
    p_imp.add_argument("--update", action="store_true", help="更新已存在的记录")
    p_imp.set_defaults(func=cmd_import)

    # scan
    p_scan = subparsers.add_parser("scan", help="扫码操作")
    p_scan.add_argument("operation", choices=["inbound", "borrow", "return", "scrap"],
                        help="操作类型: inbound入库/borrow借出/return归还/scrap报废")
    p_scan.add_argument("--codes", nargs="+", help="玩具编号列表")
    p_scan.add_argument("--qty", type=int, default=1, help="操作数量")
    p_scan.add_argument("--operator", help="操作人")
    p_scan.add_argument("--location", help="目标库位 (入库时)")
    p_scan.add_argument("--shelf", help="目标货架 (入库时)")
    p_scan.add_argument("--name", help="新玩具名称 (入库时)")
    p_scan.add_argument("--category", help="新玩具分类 (入库时)")
    p_scan.add_argument("--brand", help="新玩具品牌 (入库时)")
    p_scan.add_argument("--age", help="适用年龄 (入库时)")
    p_scan.add_argument("--price", type=float, help="单价 (入库时)")
    p_scan.add_argument("-b", "--borrower", help="借出人姓名 (借出时)")
    p_scan.add_argument("--due", help="应还日期 YYYY-MM-DD (借出时)")
    p_scan.add_argument("--condition", choices=["完好", "轻微破损", "待维修", "破损"], help="归还状况")
    p_scan.add_argument("--reason", help="报废原因 (报废时)")
    p_scan.set_defaults(func=cmd_scan)

    # move
    p_move = subparsers.add_parser("move", help="库位调拨")
    p_move.add_argument("--codes", nargs="+", help="玩具编号列表")
    p_move.add_argument("--all-from", help="整库调拨源库位")
    p_move.add_argument("--to", required=True, help="目标库位")
    p_move.add_argument("--shelf", help="目标货架")
    p_move.add_argument("--operator", help="操作人")
    p_move.set_defaults(func=cmd_move)

    # check
    p_check = subparsers.add_parser("check", help="盘点检查")
    p_check.add_argument("--location", help="按库位盘点")
    p_check.add_argument("--all", action="store_true", help="全部盘点")
    p_check.add_argument("--borrowed", action="store_true", help="查看借出中")
    p_check.add_argument("--overdue", action="store_true", help="查看逾期未还")
    p_check.set_defaults(func=cmd_check)

    # report
    p_rep = subparsers.add_parser("report", help="统计报告")
    p_rep.add_argument("type", choices=["diff", "age", "category", "logs"],
                       help="报告类型: diff差异/age库龄/category分类/logs日志")
    p_rep.add_argument("--file", help="台账基准文件 (diff时用)")
    p_rep.add_argument("--detail", type=int, help="库龄明细条数")
    p_rep.add_argument("--limit", type=int, help="日志条数限制")
    p_rep.set_defaults(func=cmd_report)

    # label
    p_lab = subparsers.add_parser("label", help="标签与设置")
    p_lab.add_argument("--codes", nargs="+", help="要打印标签的编号")
    p_lab.add_argument("--all", action="store_true", help="打印全部标签")
    p_lab.add_argument("--cols", type=int, default=2, help="每行排列数")
    p_lab.add_argument("--output", help="输出文件")
    p_lab.add_argument("--safety", action="store_true", help="设置安全库存")
    p_lab.add_argument("--category", help="分类名")
    p_lab.add_argument("--threshold", type=int, help="安全阈值")
    p_lab.add_argument("--list", action="store_true", help="查看安全库存列表")
    p_lab.add_argument("--operator", help="设置默认操作人")
    p_lab.add_argument("--overdue", type=int, help="设置逾期阈值(天)")
    p_lab.set_defaults(func=cmd_label)

    # export
    p_exp = subparsers.add_parser("export", help="导出盘点表")
    p_exp.add_argument("--format", choices=["csv", "json", "txt", "all"], default="all",
                       help="导出格式 (默认all)")
    p_exp.add_argument("--output", help="输出文件名前缀")
    p_exp.set_defaults(func=cmd_export)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n操作已取消")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
