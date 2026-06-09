#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
玩具仓库盘点命令行工具 V2 - 适用于幼儿园或玩具租赁仓
支持细分数量口径(总库存/在库/借出/维修/报废)、借出明细台账、盘点会话
"""

import argparse
import json
import csv
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict


BASE_DIR = Path.cwd()
CONFIG_FILE = BASE_DIR / "config.json"
INVENTORY_FILE = BASE_DIR / "inventory.json"
LOGS_FILE = BASE_DIR / "logs.json"
BORROW_FILE = BASE_DIR / "borrow_records.json"
COUNT_FILE = BASE_DIR / "count_sessions.json"


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
    inv = load_json(INVENTORY_FILE, {"items": {}})
    if "items" not in inv:
        inv["items"] = {}
    for code, it in inv["items"].items():
        if "qty_total" not in it:
            old_qty = it.get("quantity", 0)
            old_status = it.get("status", "在库")
            it["qty_total"] = max(old_qty, 0)
            if old_status == "借出":
                it["qty_borrowed"] = old_qty
                it["qty_available"] = 0
            elif old_status == "报废":
                it["qty_scrapped"] = old_qty
                it["qty_available"] = 0
                it["qty_total"] = 0
            elif old_status == "待维修":
                it["qty_repair"] = old_qty
                it["qty_available"] = 0
            else:
                it["qty_available"] = old_qty
                it["qty_borrowed"] = 0
                it["qty_repair"] = 0
                it["qty_scrapped"] = 0
            it["status"] = recompute_status(it)
            it["borrower"] = ""
            it["borrow_date"] = ""
            it["due_date"] = ""
            it["quantity"] = it["qty_available"]
    return inv


def save_inventory(inv):
    for code, it in inv["items"].items():
        it["quantity"] = it.get("qty_available", 0)
    save_json(INVENTORY_FILE, inv)


def get_logs():
    return load_json(LOGS_FILE, {"records": []})


def save_logs(logs):
    save_json(LOGS_FILE, logs)


def get_borrow_records():
    return load_json(BORROW_FILE, {"records": []})


def save_borrow_records(data):
    save_json(BORROW_FILE, data)


def get_count_sessions():
    return load_json(COUNT_FILE, {"sessions": [], "active_id": None})


def save_count_sessions(data):
    save_json(COUNT_FILE, data)


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


def new_id(prefix=""):
    return prefix + str(uuid.uuid4())[:8].upper()


def recompute_status(item):
    qb = item.get("qty_borrowed", 0)
    qr = item.get("qty_repair", 0)
    qa = item.get("qty_available", 0)
    qs = item.get("qty_scrapped", 0)
    if qa == 0 and qb == 0 and qr == 0 and (qs > 0 or item.get("qty_total", 0) == 0):
        return "报废"
    if qr > 0 and qa == 0 and qb == 0:
        return "待维修"
    if qb > 0 and qa == 0:
        return "借出"
    return "在库"


def short_status_text(item):
    parts = []
    for k, name in [("qty_available", "在库"), ("qty_borrowed", "借出"), ("qty_repair", "维修"), ("qty_scrapped", "报废")]:
        v = item.get(k, 0)
        if v > 0:
            parts.append(f"{name}{v}")
    return "/".join(parts) if parts else "空"


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


def find_active_session():
    cs = get_count_sessions()
    aid = cs.get("active_id")
    if aid:
        for s in cs["sessions"]:
            if s["id"] == aid and s["status"] != "closed":
                return cs, s
    return cs, None


# ==================== init 命令 ====================

def cmd_init(args):
    cfg = get_config()
    if cfg.get("initialized") and not args.force:
        print("⚠ 仓库已初始化。如需重新初始化请使用 --force 参数")
        return
    if args.force and cfg.get("initialized") and not args.yes:
        ans = input("确认强制初始化? 会重置库位配置但不清空库存 [y/N]: ").strip().lower()
        if ans != "y":
            print("已取消")
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
    log_action("INIT", f"初始化库位: {len(locations)}个区域, 每区{shelves_per_loc}层货架, {rows}行{cols}列")

    print(f"\n✅ 初始化成功！共创建 {len(locations)} 个库位区域：")
    headers = ["库位编号", "描述", "货架列表", "容量"]
    table_rows = [[code, loc["description"], ", ".join(loc["shelves"]), loc["capacity"]]
                  for code, loc in locations.items()]
    print_table(headers, table_rows)


# ==================== import 命令 ====================

def cmd_import(args):
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
        exists = code in items
        if exists and not args.update:
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
        try:
            price = float(toy.get("单价") or toy.get("price") or 0)
        except (ValueError, TypeError):
            price = 0.0

        old = items.get(code, {})
        qb_old = old.get("qty_borrowed", 0)
        qr_old = old.get("qty_repair", 0)
        qs_old = old.get("qty_scrapped", 0)
        if exists:
            qa = quantity
            qt = quantity + qb_old + qr_old
        else:
            qa = quantity
            qb_old = 0
            qr_old = 0
            qs_old = 0
            qt = quantity

        new_item = {
            "code": code,
            "name": toy.get("名称") or toy.get("name") or old.get("name") or f"玩具{code}",
            "category": toy.get("分类") or toy.get("category") or old.get("category") or "未分类",
            "brand": toy.get("品牌") or toy.get("brand") or old.get("brand") or "",
            "age_range": toy.get("适用年龄") or toy.get("age") or old.get("age_range") or "",
            "location": location,
            "shelf": shelf or old.get("shelf") or "",
            "price": price,
            "condition": old.get("condition", "完好"),
            "qty_total": qt,
            "qty_available": qa,
            "qty_borrowed": qb_old,
            "qty_repair": qr_old,
            "qty_scrapped": qs_old,
            "quantity": qa,
            "inbound_date": toy.get("入库日期") or toy.get("inbound_date") or old.get("inbound_date") or now_str()[:10],
            "last_scan": old.get("last_scan", ""),
            "scan_count": old.get("scan_count", 0),
            "remarks": toy.get("备注") or toy.get("remarks") or old.get("remarks") or "",
            "borrower": "",
            "borrow_date": "",
            "due_date": ""
        }
        new_item["status"] = recompute_status(new_item)
        items[code] = new_item
        imported += 1

    inv["items"] = items
    save_inventory(inv)
    log_action("IMPORT", f"导入玩具台账: 成功{imported}条, 跳过{skipped}条, 来源: {filepath.name}")

    print(f"\n✅ 导入完成！成功 {imported} 条，跳过 {skipped} 条")
    if imported > 0:
        print("\n前5条记录预览：")
        headers = ["编号", "名称", "分类", "库位", "总库存", "在库", "借出", "维修", "报废"]
        rows = []
        for i, (code, item) in enumerate(list(items.items())[-imported:]):
            if i >= 5:
                break
            rows.append([code, item["name"], item["category"], item["location"],
                         item.get("qty_total", 0), item.get("qty_available", 0),
                         item.get("qty_borrowed", 0), item.get("qty_repair", 0), item.get("qty_scrapped", 0)])
        print_table(headers, rows)


# ==================== scan 命令 (V2重写) ====================

def cmd_scan(args):
    cfg = get_config()
    inv = get_inventory()
    items = inv["items"]
    borrow_db = get_borrow_records()
    borrow_list = borrow_db["records"]

    op = args.operation
    if op == "followup":
        cmd_scan_followup(args)
        return
    operator = args.operator or cfg.get("operator", "system")
    codes = args.codes if args.codes else []
    qty_per = max(1, args.qty or 1)

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

    # ========= 审批处理（--approve / --reject） =========
    if args.approve or args.reject:
        action = "approve" if args.approve else "reject"
        approval_text = "approved" if args.approve else "rejected"
        for code in codes:
            candidates = [r for r in borrow_list if r["code"] == code and r.get("approval_status") == "pending"]
            if not candidates:
                failed.append((code, "无待审批借出记录"))
                continue
            if args.borrower:
                candidates = [r for r in candidates if r["borrower"] == args.borrower]
            if not candidates:
                failed.append((code, f"无{args.borrower}的待审批记录"))
                continue
            for rec in candidates:
                old_status = rec.get("approval_status", "pending")
                rec["approval_status"] = approval_text
                rec["status"] = "active" if action == "approve" else "rejected"
                if action == "approve" and old_status == "pending":
                    qty_pending = rec["qty"] - rec["qty_returned"]
                    it = items[code]
                    if qty_pending <= it.get("qty_available", 0):
                        it["qty_available"] -= qty_pending
                        it["qty_borrowed"] += qty_pending
                    else:
                        failed.append((code, f"审批时可用库存不足: 需{qty_pending}/在库{it.get('qty_available',0)}"))
                        rec["approval_status"] = old_status
                        rec["status"] = "pending"
                        continue
                rec.setdefault("history", []).append({
                    "time": now_str(),
                    "action": action,
                    "operator": operator,
                    "detail": f"审批 {'通过' if action == 'approve' else '拒绝'}" + (f": {args.remark}" if args.remark else "")
                })
                success.append(code)
                details_list.append(f"[{rec['id']}]{code}审批{'通过' if action == 'approve' else '拒绝'} - {rec['borrower']}x{rec['qty']}")
        save_inventory(inv)
        save_borrow_records(borrow_db)
        log_action(f"SCAN-APPROVE-{action.upper()}", f"成功{len(success)}件; {'; '.join(details_list[:500])}", operator)
        print(f"\n📊 审批结果操作人: {operator}")
        print(f"  成功: {len(success)} 件")
        if failed:
            print(f"  失败: {len(failed)} 件")
            for c, r in failed: print(f"    - {c}: {r}")
        for d in details_list[:8]:
            print(f"    • {d}")
        return

    for code in codes:
        # ========= 入库 =========
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
                it = items[code]
                it["qty_available"] += qty_per
                it["qty_total"] += qty_per
                it["location"] = loc
                it["shelf"] = shelf
                if it["qty_available"] > 0:
                    it["condition"] = "完好"
            else:
                name = args.name or f"玩具{code}"
                items[code] = {
                    "code": code, "name": name,
                    "category": args.category or "未分类",
                    "brand": args.brand or "", "age_range": args.age or "",
                    "location": loc, "shelf": shelf,
                    "price": args.price or 0, "condition": "完好",
                    "status": "在库", "borrower": "", "borrow_date": "", "due_date": "",
                    "qty_total": qty_per, "qty_available": qty_per,
                    "qty_borrowed": 0, "qty_repair": 0, "qty_scrapped": 0,
                    "quantity": qty_per,
                    "inbound_date": now_str()[:10],
                    "last_scan": now_str(), "scan_count": 1, "remarks": ""
                }
            items[code]["last_scan"] = now_str()
            items[code]["scan_count"] = items[code].get("scan_count", 0) + 1
            items[code]["status"] = recompute_status(items[code])
            success.append(code)
            details_list.append(f"{code}入库+{qty_per}: 库位{loc}/{shelf}")

        # ========= 借出 =========
        elif op == "borrow":
            if code not in items:
                failed.append((code, "编号不存在"))
                continue
            it = items[code]
            available = it.get("qty_available", 0)
            if available <= 0:
                failed.append((code, f"可用库存为0 (总口径:{short_status_text(it)})"))
                continue
            if qty_per > available:
                failed.append((code, f"超出可用库存: 借{qty_per}/在库{available}"))
                continue
            if not args.borrower:
                failed.append((code, "未指定借出人(-b)"))
                continue

            overdue_days = cfg.get("overdue_days", 30)
            due_date_str = args.due or (datetime.now() + timedelta(days=overdue_days)).strftime("%Y-%m-%d")
            borrow_date_str = now_str()[:10]
            approval_status = args.approval or "approved"
            purpose = args.purpose or ""
            handler = args.handler or operator
            contact = args.contact or ""
            first_remark = args.remark or ""

            rec_id = new_id("BR")
            history = [{
                "time": now_str(),
                "action": "create",
                "operator": operator,
                "detail": f"创建借出单, 数量{qty_per}, 到期{due_date_str}"
            }]
            if first_remark:
                history.append({"time": now_str(), "action": "remark", "operator": operator, "detail": f"备注: {first_remark}"})
            if approval_status != "approved":
                history.append({"time": now_str(), "action": f"set_{approval_status}", "operator": operator,
                                "detail": f"状态设为 {approval_status}"})

            borrow_list.append({
                "id": rec_id,
                "code": code,
                "toy_name": it["name"],
                "borrower": args.borrower,
                "qty": qty_per,
                "qty_returned": 0,
                "borrow_date": borrow_date_str,
                "due_date": due_date_str,
                "original_due": due_date_str,
                "status": "active" if approval_status == "approved" else (approval_status if approval_status == "rejected" else "pending"),
                "approval_status": approval_status,
                "operator": operator,
                "handler": handler,
                "purpose": purpose,
                "contact": contact,
                "renew_count": 0,
                "returns": [],
                "history": history,
                "remarks": first_remark
            })

            if approval_status == "approved":
                it["qty_available"] -= qty_per
                it["qty_borrowed"] += qty_per
            it["last_scan"] = now_str()
            it["scan_count"] += 1
            it["status"] = recompute_status(it)
            it["borrower"] = ""
            it["borrow_date"] = ""
            it["due_date"] = ""

            success.append(code)
            extra = f"[审批:{approval_status}]" if approval_status != "approved" else ""
            details_list.append(f"[{rec_id}]{code}x{qty_per}借给{args.borrower}{extra},到期{due_date_str},用途:{purpose or '未填'},经手:{handler}")

        # ========= 归还 =========
        elif op == "return":
            if code not in items:
                failed.append((code, "编号不存在"))
                continue
            it = items[code]
            candidates = [r for r in borrow_list
                          if r["code"] == code and r["status"] in ("active", "partial", "overdue")]
            if not candidates:
                failed.append((code, "无未结清借出记录"))
                continue
            if args.borrower:
                match = [r for r in candidates if r["borrower"] == args.borrower]
                if match:
                    candidates = match
            candidates.sort(key=lambda r: r["borrow_date"])

            returned_total = 0
            remaining_needed = qty_per
            rec_ids_used = []
            condition = args.condition or "完好"
            to_repair = (condition in ("轻微破损", "待维修", "破损"))

            for rec in candidates:
                if remaining_needed <= 0:
                    break
                outstanding = rec["qty"] - rec["qty_returned"]
                take = min(remaining_needed, outstanding)
                rec["qty_returned"] += take
                remaining_needed -= take
                returned_total += take
                rec_ids_used.append(f"{rec['id']}x{take}")
                return_detail = f"归还{take}件, 状况={condition}"
                if args.remark:
                    return_detail += f", 备注={args.remark}"
                rec["returns"].append({
                    "date": now_str()[:10],
                    "qty": take,
                    "condition": condition,
                    "operator": operator,
                    "remark": args.remark or ""
                })
                rec.setdefault("history", []).append({
                    "time": now_str(),
                    "action": "return",
                    "operator": operator,
                    "detail": return_detail
                })
                rec["status"] = "closed" if rec["qty_returned"] >= rec["qty"] else "partial"

                if rec["status"] == "closed":
                    rec["history"].append({
                        "time": now_str(),
                        "action": "close",
                        "operator": operator,
                        "detail": "全部归还完毕, 借出单关闭"
                    })
                    flow_text_parts = []
                    for h in rec.get("history", []):
                        flow_text_parts.append(f"  [{h['time'][:16]}] {h.get('operator','')} {h['action']}: {h['detail']}")
                    flow_text = "\n".join(flow_text_parts)
                    details_list.append(
                        f"[{rec['id']}]{code}流转记录({rec['borrower']}x{rec['qty']}):\n{flow_text}")

            if returned_total <= 0:
                failed.append((code, "没有可归还的数量"))
                continue

            if to_repair:
                it["qty_repair"] += returned_total
                it["condition"] = condition
            else:
                it["qty_available"] += returned_total
                it["condition"] = "完好"
            it["qty_borrowed"] -= returned_total
            it["last_scan"] = now_str()
            it["scan_count"] += 1
            it["status"] = recompute_status(it)

            # 全部归还完毕则清理
            if it["qty_borrowed"] == 0:
                it["borrower"] = ""
                it["borrow_date"] = ""
                it["due_date"] = ""

            success.append(code)
            suffix = f" 还差{remaining_needed}件" if remaining_needed > 0 else ""
            details_list.append(f"{code}归还{returned_total}件({condition}): {','.join(rec_ids_used)}{suffix}")

        # ========= 续借 =========
        elif op == "renew":
            if code not in items:
                failed.append((code, "编号不存在"))
                continue
            candidates = [r for r in borrow_list
                          if r["code"] == code and r["status"] in ("active", "partial", "overdue")]
            if not candidates:
                failed.append((code, "无进行中的借出记录"))
                continue
            if args.borrower:
                match = [r for r in candidates if r["borrower"] == args.borrower]
                if match:
                    candidates = match
            days = args.renew_days or cfg.get("overdue_days", 30)
            for rec in candidates:
                old_due = rec["due_date"]
                try:
                    base = max(datetime.now().date(), datetime.strptime(rec["due_date"], "%Y-%m-%d").date())
                except ValueError:
                    base = datetime.now().date()
                new_due = (base + timedelta(days=days)).strftime("%Y-%m-%d")
                rec["due_date"] = new_due
                rec["renew_count"] += 1
                rec["status"] = "active"
                renew_detail = f"续借{days}天 {old_due}→{new_due}"
                if args.remark:
                    renew_detail += f", 备注={args.remark}"
                rec.setdefault("history", []).append({
                    "time": now_str(),
                    "action": "renew",
                    "operator": operator,
                    "detail": renew_detail
                })
                details_list.append(f"[{rec['id']}]{code}续借{days}天 {old_due}→{new_due} ({rec['borrower']})")
            success.append(code)
            items[code]["last_scan"] = now_str()
            items[code]["scan_count"] += 1

        # ========= 报废 =========
        elif op == "scrap":
            if code not in items:
                failed.append((code, "编号不存在"))
                continue
            it = items[code]
            available = it.get("qty_available", 0)
            repair = it.get("qty_repair", 0)
            if args.from_repair:
                if qty_per > repair:
                    failed.append((code, f"维修中仅{repair}件, 不足以报废{qty_per}"))
                    continue
                it["qty_repair"] -= qty_per
            else:
                if qty_per > available:
                    failed.append((code, f"在库可用仅{available}件, 不足以报废{qty_per}(维修报废加--from-repair)"))
                    continue
                it["qty_available"] -= qty_per

            reason = args.reason or "无原因"
            it["qty_scrapped"] += qty_per
            it["qty_total"] = max(0, it.get("qty_total", 0) - qty_per)
            it["condition"] = "破损"
            it["last_scan"] = now_str()
            it["scan_count"] += 1
            it["status"] = recompute_status(it)
            it["remarks"] = f"{now_str()[:10]}报废{qty_per}件:{reason} | " + (it.get("remarks") or "")
            success.append(code)
            details_list.append(f"{code}报废{qty_per}件(原因:{reason})")

    save_inventory(inv)
    save_borrow_records(borrow_db)
    log_action(
        f"SCAN-{op.upper()}",
        f"操作人:{operator}; 成功{len(success)}件[{','.join(success[:15])}]; 失败{len(failed)}件; {'; '.join(details_list[:800])}",
        operator
    )

    print(f"\n📊 操作结果【{op}】操作人: {operator}")
    print(f"  成功: {len(success)} 件 {success[:12]}{'...' if len(success) > 12 else ''}")
    if failed:
        print(f"  失败: {len(failed)} 件")
        for c, r in failed:
            print(f"    - {c}: {r}")
    if details_list:
        print(f"\n  明细:")
        for d in details_list[:12]:
            print(f"    • {d}")


# ==================== move 命令 ====================

def cmd_move(args):
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
            if item["location"] == args.all_from:
                item["location"] = args.to
                item["shelf"] = target_shelf
                moved += 1
        save_inventory(inv)
        log_action("MOVE-ALL", f"整库调拨 {args.all_from} -> {args.to}/{target_shelf}, {moved}件", operator)
        print(f"✅ 整库调拨完成：从 {args.all_from} 调拨 {moved} 件到 {args.to}/{target_shelf}")
        return

    if not args.codes:
        print("❌ 请指定要调拨的玩具编号 (--codes) 或整库调拨 (--all-from)")
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
    log_action("MOVE", f"操作人:{operator}; 成功{len(success)}件; {details[:800]}", operator)

    print(f"\n📦 库位调拨结果  操作人: {operator}")
    print(f"  成功: {len(success)} 件")
    headers = ["编号", "名称", "原库位", "新库位", "数量口径"]
    rows = [[c, items[c]["name"], o, f"{args.to}/{target_shelf}", short_status_text(items[c])] for c, o in success]
    print_table(headers, rows)
    if failed:
        for c, r in failed:
            print(f"  失败 - {c}: {r}")


# ==================== 盘点会话核心 ====================

def cmd_count_start(args):
    cfg = get_config()
    operator = args.operator or cfg.get("operator", "system")
    cs_obj, active = find_active_session()
    if active and not args.force:
        print(f"⚠ 已有进行中的盘点会话 [{active['id']}] (库位:{active.get('location','全部')})")
        print("  请先 count-close 或 count-abort")
        return

    loc = args.location
    if loc and loc not in cfg["locations"]:
        print(f"❌ 库位 {loc} 不存在")
        return

    cs_obj = get_count_sessions()
    session_id = new_id("CNT")
    session = {
        "id": session_id,
        "started_at": now_str(),
        "finished_at": None,
        "operator": operator,
        "location": loc or "ALL",
        "status": "draft",
        "stage": "scanning",
        "counts": {},
        "review_counts": {},
        "reviewer": None,
        "reviewed_at": None,
        "book_snapshot": {},
        "adjustments_made": False,
        "note": args.note or ""
    }
    inv = get_inventory()
    for code, it in inv["items"].items():
        if loc and it["location"] != loc:
            continue
        qt = it.get("qty_total", 0)
        qa = it.get("qty_available", 0)
        qb = it.get("qty_borrowed", 0)
        qr = it.get("qty_repair", 0)
        qs = it.get("qty_scrapped", 0)
        session["book_snapshot"][code] = {
            "name": it["name"], "category": it["category"],
            "location": it["location"], "shelf": it["shelf"],
            "qty_total": qt, "qty_available": qa, "qty_borrowed": qb,
            "qty_repair": qr, "qty_scrapped": qs,
            "qty_onhand_book": qa + qr   # 应在场 = 在库+维修 (借出不在场)
        }
    cs_obj["sessions"].append(session)
    cs_obj["active_id"] = session_id
    save_count_sessions(cs_obj)
    log_action("COUNT-START", f"开启会话{session_id}, 库位={session['location']}, 账面{len(session['book_snapshot'])}个品种", operator)

    print(f"\n✅ 盘点会话已开启 [{session_id}]")
    print(f"  库位范围: {session['location']}  操作人: {operator}")
    print(f"  账面应盘: {len(session['book_snapshot'])} 个品种")
    print(f"\n  下一步命令:")
    print(f"    count-scan --interactive                 # 交互式扫码录入实盘")
    print(f"    count-scan T001 10 T002 8 ...            # 命令行批量录入")
    print(f"    count-diff                                # 查看账面vs实盘差异")
    print(f"    count-close [--apply]                     # 关闭(可选一键调账)")


def cmd_count_scan(args):
    cs_obj, session = find_active_session()
    if not session:
        print("❌ 没有进行中的盘点会话，请先 count-start")
        return

    entries = args.entries
    pairs = []
    if entries:
        i = 0
        while i < len(entries):
            raw = entries[i].strip()
            if not raw:
                i += 1
                continue
            parts = raw.split()
            code = parts[0]
            qty = 1
            if len(parts) > 1 and parts[1].lstrip("-").isdigit():
                qty = int(parts[1])
                i += 1
            else:
                if i + 1 < len(entries) and entries[i + 1].lstrip("-").isdigit():
                    qty = int(entries[i + 1])
                    i += 2
                else:
                    i += 1
            pairs.append((code, qty))

    if args.interactive:
        print(f"\n📋 盘点会话 [{session['id']}] 实盘录入 (输入 q 或空行结束)")
        print("  格式: <编号> [数量]   例: T001 5")
        while True:
            try:
                line = input("> ").strip()
                if line.lower() in ("q", "quit", "exit", ""):
                    break
                parts = line.split()
                code = parts[0]
                qty = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 1
                pairs.append((code, qty))
            except (EOFError, ValueError):
                continue

    if not pairs:
        print("❌ 未录入任何数据")
        return

    updated = new_found = 0
    inv = None
    for code, qty in pairs:
        if code in session["counts"]:
            session["counts"][code]["qty"] = qty
        else:
            snap = session["book_snapshot"].get(code)
            if snap:
                session["counts"][code] = {"qty": qty, "name": snap["name"]}
            else:
                if inv is None:
                    inv = get_inventory()
                name = inv["items"].get(code, {}).get("name", f"未知({code})")
                session["counts"][code] = {"qty": qty, "name": name, "extra": True}
                new_found += 1
        updated += 1
    session["last_updated"] = now_str()
    save_count_sessions(cs_obj)
    log_action("COUNT-SCAN", f"会话{session['id']}录入{len(pairs)}条, 更新{updated}, 盘盈{new_found}")
    print(f"\n✅ 已录入 {len(pairs)} 条  更新:{updated}  盘盈新编号:{new_found}")
    print(f"  进度: 已盘 {len(session['counts'])} / 账面应盘 {len(session['book_snapshot'])}")


def analyze_diff(session):
    """
    通用差异分析（可用于进行中会话 / 历史会话回看 / 导出）
    返回: dict {summary, rows_by_type, cols_all}
      rows_by_type: matched / matched_reviewed / missing / missing_reviewed /
                    diff_rows / diff_reviewed / extra / extra_reviewed
      cols_all: 每条记录完整 dict 用于导出: code,name,book_onhand,book_qa,book_qr,
                          count_qty, review_qty, effective_qty, diff, status, category
    """
    book = session["book_snapshot"]
    actual = session.get("counts", {})
    review = session.get("review_counts", {})

    def get_effective(code):
        r = review.get(code)
        if r: return r["qty"]
        a = actual.get(code)
        if a: return a["qty"]
        return 0

    result = {
        "summary": {},
        "matched": [], "matched_reviewed": [],
        "missing": [], "missing_reviewed": [],
        "diff_rows": [], "diff_reviewed": [],
        "extra": [], "extra_reviewed": [],
        "cols_all": [],
        "total_book": 0, "total_actual": 0
    }
    tot_book = tot_eff = 0

    for code, b in book.items():
        a = actual.get(code)
        r = review.get(code)
        eff = get_effective(code)
        book_onhand = b["qty_onhand_book"]
        tot_book += book_onhand
        tot_eff += eff
        diff = eff - book_onhand
        has_count = (a is not None)
        has_review = (r is not None)
        col = {
            "code": code, "name": b["name"],
            "book_qa": b["qty_available"], "book_qr": b["qty_repair"],
            "book_onhand": book_onhand,
            "count_qty": a["qty"] if a else None,
            "review_qty": r["qty"] if r else None,
            "effective_qty": eff, "diff": diff,
            "category": b.get("category", "")
        }

        if not has_count and not has_review:
            # 真·未盘到：初盘复核都没扫
            base = [code, b["name"], f"{b['qty_available']}+{b['qty_repair']}", book_onhand, 0]
            if session.get("stage") == "reviewing" or review:
                tag = "未盘到(确认盘亏)" if review else "未盘到(待复核)"
                status = "missing_confirmed" if review else "missing_pending"
                row = base + [f"-{book_onhand}", tag]
                (result["missing_reviewed"] if review else result["missing"]).append(row)
                col["status"] = status
            else:
                result["missing"].append(base + [f"-{book_onhand}", "未盘到"])
                col["status"] = "missing_pending"
        elif diff != 0:
            sign = "+" if diff > 0 else ""
            base_tag = "盘盈" if diff > 0 else "盘亏"
            base = [code, b["name"], f"{b['qty_available']}+{b['qty_repair']}", book_onhand, eff]
            if has_review:
                row = base + [f"{sign}{diff}", f"{base_tag}(复核确认)"]
                result["diff_reviewed"].append(row)
                col["status"] = f"{base_tag.lower()}_reviewed"
            else:
                row = base + [f"{sign}{diff}", base_tag]
                result["diff_rows"].append(row)
                col["status"] = f"{base_tag.lower()}_pending"
        else:
            if has_review:
                result["matched_reviewed"].append(code)
                col["status"] = "matched_reviewed"
            else:
                result["matched"].append(code)
                col["status"] = "matched"
        result["cols_all"].append(col)

    # 盘盈（台账无）
    for code in list(actual.keys()) + [c for c in review if c not in actual]:
        if code in book: continue
        r = review.get(code)
        a = actual.get(code)
        qty = r["qty"] if r else (a["qty"] if a else 0)
        if qty == 0: continue
        name = (r or a or {}).get("name", f"未知({code})")
        tot_eff += qty
        base = [code, name, "-", 0, qty]
        col = {
            "code": code, "name": name, "book_qa": 0, "book_qr": 0, "book_onhand": 0,
            "count_qty": a["qty"] if a else None,
            "review_qty": r["qty"] if r else None,
            "effective_qty": qty, "diff": qty, "category": "盘盈待归类"
        }
        if r:
            result["extra_reviewed"].append(base + [f"+{qty}", "盘盈(新)(复核确认)"])
            col["status"] = "extra_reviewed"
        else:
            result["extra"].append(base + [f"+{qty}", "盘盈(新)"])
            col["status"] = "extra_pending"
        result["cols_all"].append(col)

    result["total_book"] = tot_book
    result["total_actual"] = tot_eff
    n = lambda x: len(x)
    result["summary"] = {
        "book_types": len(book), "book_qty": tot_book,
        "actual_types": len(actual) + len(review) - len(set(actual) & set(review)),
        "actual_qty": tot_eff,
        "matched": n(result["matched"]),
        "matched_reviewed": n(result["matched_reviewed"]),
        "diff_pending": n(result["diff_rows"]),
        "diff_reviewed": n(result["diff_reviewed"]),
        "missing_pending": n(result["missing"]),
        "missing_confirmed": n(result["missing_reviewed"]),
        "extra_pending": n(result["extra"]),
        "extra_reviewed": n(result["extra_reviewed"]),
    }
    return result


def cmd_count_diff(args):
    cs_obj, session = find_active_session()
    if not session:
        # 支持对历史会话回看 diff：check count-diff --id CNTxxx
        if getattr(args, "id", None):
            all_s = get_count_sessions()
            session = next((s for s in all_s["sessions"] if s["id"] == args.id), None)
        if not session:
            print("❌ 没有指定的盘点会话，请先 count-start 或用 --id 指定历史会话")
            return

    stage = session.get("stage", "scanning")
    reviewer = session.get("reviewer")
    print(f"\n🔍 盘点差异报告 [{session['id']}] 库位:{session.get('location','ALL')}")
    print(f"  操作人: {session['operator']}  开始: {session['started_at']}  阶段: {stage}")
    if reviewer:
        print(f"  复核人: {reviewer}  复核时间: {session.get('reviewed_at')}")
    if session.get("finished_at"):
        print(f"  结束时间: {session['finished_at']}  已调账: {'是' if session.get('adjustments_made') else '否'}")

    res = analyze_diff(session)
    s = res["summary"]
    print(f"\n  汇总: 账面在场{s['book_types']}个品种/{s['book_qty']}件 → 实盘在场{s['actual_types']}个品种/{s['actual_qty']}件")
    print(f"         ✔ 相符:{s['matched']}  ✔复核后相符:{s['matched_reviewed']}  "
          f"⚠ 待复核数量差:{s['diff_pending']}  ✔已复核差异:{s['diff_reviewed']}")
    print(f"         ❌ 未盘到:{s['missing_pending']}  ❌复核后确认未盘到:{s['missing_confirmed']}  "
          f"➕ 待复核盘盈:{s['extra_pending']}  ✔已复核盘盈:{s['extra_reviewed']}")

    if res["missing"]:
        print(f"\n❌ 未盘到 (账面在场但没扫到, 待复核): {len(res['missing'])}")
        print_table(["编号", "名称", "细分(在库+维修)", "账面在场", "实盘", "差异", "状态"],
                    res["missing"][:args.limit] if args.limit else res["missing"])
    if res["missing_reviewed"]:
        print(f"\n❌ 未盘到 (复核后确认按盘亏): {len(res['missing_reviewed'])}")
        print_table(["编号", "名称", "细分(在库+维修)", "账面在场", "实盘", "差异", "状态"],
                    res["missing_reviewed"][:args.limit] if args.limit else res["missing_reviewed"])
    if res["diff_rows"]:
        print(f"\n⚠ 数量不符(待复核确认): {len(res['diff_rows'])}")
        print_table(["编号", "名称", "细分(在库+维修)", "账面在场", "实盘", "差异", "判定"],
                    res["diff_rows"][:args.limit] if args.limit else res["diff_rows"])
    if res["diff_reviewed"]:
        print(f"\n⚠ 数量不符(复核后已确认): {len(res['diff_reviewed'])}")
        print_table(["编号", "名称", "细分(在库+维修)", "账面在场", "实盘", "差异", "判定"],
                    res["diff_reviewed"][:args.limit] if args.limit else res["diff_reviewed"])
    if res["extra"]:
        print(f"\n➕ 盘盈(待复核确认): {len(res['extra'])}")
        print_table(["编号", "名称", "细分", "账面在场", "实盘", "差异", "判定"],
                    res["extra"][:args.limit] if args.limit else res["extra"])
    if res["extra_reviewed"]:
        print(f"\n➕ 盘盈(复核后已确认): {len(res['extra_reviewed'])}")
        print_table(["编号", "名称", "细分", "账面在场", "实盘", "差异", "判定"],
                    res["extra_reviewed"][:args.limit] if args.limit else res["extra_reviewed"])

    if all(len(v) == 0 for k, v in res.items() if k in ("missing", "missing_reviewed", "diff_rows", "diff_reviewed", "extra", "extra_reviewed")):
        print("\n✅ 账实完全相符！")
    elif stage == "scanning" and not session.get("finished_at"):
        print("\n💡 提示: 初盘录入完成后可用 'check count-review' 进入复核环节, 对差异部分补扫确认")

    # 已关闭会话 -> 显示调账明细
    if session.get("finished_at") and session.get("adjustments_made"):
        print(f"\n📌 本次调账共 {session.get('adjust_count', 0)} 个品种, 净差异 {session.get('adjust_qty_net', 0):+d} 件")


def cmd_count_review(args):
    cs_obj, session = find_active_session()
    if not session:
        print("❌ 没有进行中的盘点会话，请先 count-start")
        return
    cfg = get_config()
    reviewer = args.reviewer or cfg.get("operator", "system")
    book = session["book_snapshot"]
    actual = session["counts"]
    review = session.get("review_counts", {})

    if args.only_diff and not args.entries and not args.interactive:
        print(f"\n📋 当前会话待复核差异 (会话: {session['id']})")
        pending_count = 0
        for code, b in book.items():
            a = actual.get(code)
            r = review.get(code)
            actual_qty = r["qty"] if r else (a["qty"] if a else 0)
            book_onhand = b["qty_onhand_book"]
            if actual_qty != book_onhand:
                pending_count += 1
                sign = "+" if actual_qty > book_onhand else ""
                status = "(未扫)" if a is None and r is None else "(有差)"
                print(f"  {code} | {b['name'][:14]:<14} | 账面{book_onhand} | 实盘{actual_qty} | 差{sign}{actual_qty-book_onhand} {status}")
        for code in list(actual.keys()) + [c for c in review if c not in actual]:
            if code in book:
                continue
            if code not in review:
                pending_count += 1
                qty = actual.get(code, review.get(code))["qty"]
                print(f"  {code} | {actual.get(code, review.get(code)).get('name','?')[:14]:<14} | 账面0 | 实盘{qty} | 差+{qty} (盘盈)")
        print(f"\n共 {pending_count} 项差异待复核。请补扫或重新确认后用 count-review 录入复核数量。")
        session["stage"] = "reviewing"
        session["reviewer"] = reviewer
        save_count_sessions(cs_obj)
        return

    entries = args.entries
    pairs = []
    if entries:
        i = 0
        while i < len(entries):
            raw = entries[i].strip()
            if not raw:
                i += 1
                continue
            parts = raw.split()
            code = parts[0]
            qty = 1
            if len(parts) > 1 and parts[1].lstrip("-").isdigit():
                qty = int(parts[1])
                i += 1
            else:
                if i + 1 < len(entries) and entries[i + 1].lstrip("-").isdigit():
                    qty = int(entries[i + 1])
                    i += 2
                else:
                    i += 1
            pairs.append((code, qty))

    if args.interactive:
        print(f"\n📋 盘点复核录入 [{session['id']}] 复核人: {reviewer}")
        print("  格式: <编号> [数量] (输入 q 或空行结束)")
        while True:
            try:
                line = input("> ").strip()
                if line.lower() in ("q", "quit", "exit", ""):
                    break
                parts = line.split()
                code = parts[0]
                qty = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 1
                pairs.append((code, qty))
            except (EOFError, ValueError):
                continue

    if not pairs and not args.only_diff:
        session["stage"] = "reviewing"
        session["reviewer"] = reviewer
        session["reviewed_at"] = now_str()
        save_count_sessions(cs_obj)
        print(f"✅ 已进入复核阶段，复核人: {reviewer}")
        return

    inv = None
    updated = 0
    inv_names = {}
    for code, qty in pairs:
        name = ""
        snap = book.get(code)
        if snap:
            name = snap["name"]
        else:
            prev = actual.get(code)
            if prev:
                name = prev.get("name", "")
            else:
                if inv is None:
                    inv = get_inventory()
                    inv_names = inv["items"]
                name = inv_names.get(code, {}).get("name", f"未知({code})")
        review[code] = {"qty": qty, "name": name, "reviewed_by": reviewer, "reviewed_at": now_str()}
        updated += 1
    session["review_counts"] = review
    session["stage"] = "reviewing"
    session["reviewer"] = reviewer
    session["reviewed_at"] = now_str()
    save_count_sessions(cs_obj)
    log_action("COUNT-REVIEW", f"会话{session['id']}复核录入{len(pairs)}条, 复核人{reviewer}", reviewer)
    print(f"\n✅ 已复核 {len(pairs)} 条  更新:{updated}")
    print(f"  进度: 已复核 {len(review)} / 账面 {len(book)}")


def cmd_count_close(args):
    cs_obj, session = find_active_session()
    if not session:
        print("❌ 没有进行中的盘点会话，请先 count-start")
        return
    cfg = get_config()
    operator = args.operator or cfg.get("operator", "system")
    apply = args.apply
    confirm = getattr(args, "confirm", False)

    # ====== 调账计算公共闭包 (返回预览/执行需要的所有信息, 不修改库存) ======
    def compute_adjustments():
        inv = get_inventory()
        items = inv["items"]
        adjust_count = 0
        adjust_qty_net = 0
        details = []
        adjustments = {}
        pre_snapshot = {}
        locs = list(cfg["locations"].keys())
        default_loc = locs[0] if locs else "A01-01"

        effective_counts = {}
        for c, a in session["counts"].items():
            effective_counts[c] = a["qty"]
        for c, r in session.get("review_counts", {}).items():
            effective_counts[c] = r["qty"]
        for c in session["book_snapshot"].keys():
            if c not in effective_counts:
                effective_counts[c] = 0

        for code, actual_qty in effective_counts.items():
            snap_info = session["book_snapshot"].get(code, {})
            count_info = session["counts"].get(code)
            review_info = session.get("review_counts", {}).get(code)
            source_info = review_info or count_info or {}
            if code in items:
                it = items[code]
                qa = it.get("qty_available", 0)
                qr = it.get("qty_repair", 0)
                qt = it.get("qty_total", 0)
                qs = it.get("qty_scrapped", 0)
                qb = it.get("qty_borrowed", 0)
                # 调前快照
                pre_snapshot[code] = {
                    "name": it["name"],
                    "qty_total": qt, "qty_available": qa,
                    "qty_borrowed": qb, "qty_repair": qr, "qty_scrapped": qs
                }
                book_onhand = qa + qr
                diff = actual_qty - book_onhand
                if diff != 0:
                    from_repair = from_available = scrapped = to_available = 0
                    if diff < 0:
                        loss = -diff
                        from_repair = min(qr, loss)
                        from_available = loss - from_repair
                        scrapped = loss
                    else:
                        to_available = diff
                    adjust_count += 1
                    adjust_qty_net += diff
                    if review_info:
                        reason = "复核后数量差" if count_info else "复核后未盘到按盘亏"
                    else:
                        reason = "未盘到按盘亏" if count_info is None else "数量差"
                    details.append(f"{code}: 在场{book_onhand}→{actual_qty} ({diff:+d}) [{reason}]")
                    adjustments[code] = {
                        "name": it["name"],
                        "book_onhand": book_onhand, "actual_qty": actual_qty, "diff": diff,
                        "scrapped": scrapped, "from_repair": from_repair,
                        "from_available": from_available, "to_available": to_available,
                        "reason": reason
                    }
            else:
                a = source_info
                pre_snapshot[code] = {
                    "name": a.get("name", f"玩具{code}"), "qty_total": 0,
                    "qty_available": 0, "qty_borrowed": 0, "qty_repair": 0, "qty_scrapped": 0
                }
                adjust_count += 1
                adjust_qty_net += actual_qty
                details.append(f"{code}: 盘盈新增{actual_qty}件")
                adjustments[code] = {
                    "name": a.get("name", f"玩具{code}"),
                    "book_onhand": 0, "actual_qty": actual_qty, "diff": actual_qty,
                    "to_available": actual_qty, "scrapped": 0,
                    "from_repair": 0, "from_available": 0,
                    "reason": "盘盈新增"
                }

        return {
            "adjust_count": adjust_count, "adjust_qty_net": adjust_qty_net,
            "details": details, "adjustments": adjustments,
            "pre_snapshot": pre_snapshot, "default_loc": default_loc
        }

    def format_adj_rows(adj_map):
        rows = []
        for code, a in adj_map.items():
            dest = []
            if a.get("from_repair", 0): dest.append(f"维修→报废{a['from_repair']}")
            if a.get("from_available", 0): dest.append(f"在库→报废{a['from_available']}")
            if a.get("to_available", 0): dest.append(f"盘盈→在库{a['to_available']}")
            if not dest:
                dest = [("盘盈" if a["diff"] > 0 else "盘亏") + f" {abs(a['diff'])}"]
            rows.append([code, a["name"], a["book_onhand"], a["actual_qty"],
                        f"{a['diff']:+d}", "; ".join(dest), a.get("reason", "")])
        return rows

    # ====== apply 预览 / 确认 ======
    if apply:
        calc = compute_adjustments()
        mode = "【预览 - 未写入库存】" if not confirm else "【执行结果】"
        print(f"\n📋 调账{mode}: "
              f"{calc['adjust_count']} 个品种, 净差异 {calc['adjust_qty_net']:+d} 件")
        if calc["adjust_count"]:
            print_table(["编号", "名称", "账面在场", "实盘", "差异", "去向", "原因"],
                       format_adj_rows(calc["adjustments"]))

        if not confirm:
            # 只预览不写入
            print(f"\n⚠  负责人确认流程:")
            print(f"   ① 请核对上面的调账预览表")
            print(f"   ② 如有错, 返回 check count-review 继续补扫修正")
            print(f"   ③ 确认无误后再次执行 (加 --confirm):")
            print(f"      check count-close --apply --confirm")
            # 保存会话(不修改inventory)
            session["stage"] = "closed_pending_confirm"
            cs_obj["active_id"] = session["id"]  # 保持active等待确认
            save_count_sessions(cs_obj)
            log_action("COUNT-APPLY-PREVIEW",
                       f"会话{session['id']}预览 {calc['adjust_count']} 个品种, "
                       f"净差{calc['adjust_qty_net']:+d}", operator)
            print(f"\nℹ  会话标记为 stage=closed_pending_confirm, 等待加 --confirm 确认调账")
            return

        # === --apply --confirm 真正调账 ===
        inv = get_inventory()
        items = inv["items"]
        default_loc = calc["default_loc"]
        # 永久保存调前快照
        session["pre_adjust_snapshot"] = calc["pre_snapshot"]

        for code, a in calc["adjustments"].items():
            pre = calc["pre_snapshot"].get(code, {})
            if code in items:
                it = items[code]
                it["qty_repair"] = max(0, pre.get("qty_repair", 0) - a.get("from_repair", 0))
                it["qty_available"] = (pre.get("qty_available", 0)
                                       - a.get("from_available", 0)
                                       + a.get("to_available", 0))
                it["qty_scrapped"] = pre.get("qty_scrapped", 0) + a.get("scrapped", 0)
                it["qty_total"] = max(0, pre.get("qty_total", 0)
                                      - a.get("scrapped", 0)
                                      + a.get("to_available", 0))
                it["status"] = recompute_status(it)
                it["last_scan"] = now_str()
                it["scan_count"] = it.get("scan_count", 0) + 1
            else:
                inv["items"][code] = {
                    "code": code,
                    "name": a["name"],
                    "category": "盘盈待归类",
                    "brand": "", "age_range": "",
                    "location": session["location"] if session["location"] in cfg["locations"] else default_loc,
                    "shelf": "", "price": 0, "condition": "完好",
                    "status": "在库", "borrower": "", "borrow_date": "", "due_date": "",
                    "qty_total": a["diff"], "qty_available": a["diff"],
                    "qty_borrowed": 0, "qty_repair": 0, "qty_scrapped": 0,
                    "quantity": a["diff"],
                    "inbound_date": now_str()[:10],
                    "last_scan": now_str(), "scan_count": 1,
                    "remarks": f"盘点盘盈新增, 来源会话{session['id']}"
                }

        save_inventory(inv)
        session["adjustments_made"] = True
        session["adjust_count"] = calc["adjust_count"]
        session["adjust_qty_net"] = calc["adjust_qty_net"]
        session["adjustments"] = calc["adjustments"]
        session["adjust_confirmed_by"] = operator
        session["adjust_confirmed_at"] = now_str()
        log_action("COUNT-APPLY-CONFIRM",
                   f"会话{session['id']}确认调整{calc['adjust_count']}个品种, "
                   f"净差{calc['adjust_qty_net']:+d}; "
                   f"{'; '.join(calc['details'][:500])}", operator)
        print(f"\n✅ 调账已写入库存! 确认人: {operator} / {session['adjust_confirmed_at']}")
        # 显示调前→调后对比
        compare_rows = []
        for code, a in calc["adjustments"].items():
            pre = calc["pre_snapshot"].get(code, {})
            if code in items:
                it = items[code]
                pre_str = f"总{pre.get('qty_total',0)} 库{pre.get('qty_available',0)} 维{pre.get('qty_repair',0)} 报{pre.get('qty_scrapped',0)}"
                post_str = f"总{it['qty_total']} 库{it['qty_available']} 维{it['qty_repair']} 报{it['qty_scrapped']}"
                compare_rows.append([code, a["name"][:14], pre_str, post_str])
        if compare_rows:
            print(f"\n📊 调账前后对比(调前→调后):")
            print_table(["编号", "名称", "调前(总/库/维/报)", "调后(总/库/维/报)"], compare_rows[:50])
    else:
        log_action("COUNT-CLOSE", f"会话{session['id']}关闭(未调账)", operator)

    session["status"] = "closed"
    session["stage"] = "closed"
    session["finished_at"] = now_str()
    cs_obj["active_id"] = None
    save_count_sessions(cs_obj)
    log_action("COUNT-CLOSE", f"关闭盘点会话{session['id']}, 是否调整={'是' if apply and confirm else '否(未apply或未confirm)'}", operator)
    print(f"\n✅ 盘点会话 [{session['id']}] 已关闭")


def cmd_count_list(args):
    cs_obj = get_count_sessions()
    sessions = cs_obj["sessions"]
    if args.id:
        for s in sessions:
            if s["id"] == args.id:
                print(f"\n📋 盘点会话详情 [{s['id']}]")
                print(f"  库位: {s.get('location', 'ALL')}    阶段: {s.get('stage', '-')}    状态: {s['status']}")
                print(f"  操作人: {s['operator']}    开始: {s['started_at']}")
                if s.get("finished_at"):
                    print(f"  结束: {s['finished_at']}  已调账: {'是' if s.get('adjustments_made') else '否'}")
                if s.get("reviewer"):
                    print(f"  复核人: {s['reviewer']}    复核时间: {s.get('reviewed_at', '-')}")
                print(f"  账面快照: {len(s.get('book_snapshot', {}))} 品种  初盘: {len(s.get('counts', {}))} 条  复核: {len(s.get('review_counts', {}))} 条")
                if s.get("note"):
                    print(f"  备注: {s['note']}")
                if s.get("adjustments_made"):
                    print(f"\n📌 调账摘要: {s.get('adjust_count', 0)} 个品种, 净调整 {s.get('adjust_qty_net', 0):+d} 件")
                    if s.get("adjustments"):
                        adj_rows = []
                        for code, a in s["adjustments"].items():
                            dest = []
                            if a.get("from_repair", 0): dest.append(f"维修→报废{a['from_repair']}")
                            if a.get("from_available", 0): dest.append(f"在库→报废{a['from_available']}")
                            if a.get("to_available", 0): dest.append(f"盘盈→在库{a['to_available']}")
                            if not dest:
                                if a.get("diff", 0) > 0: dest.append(f"盘盈{a['diff']:+d}")
                                else: dest.append(f"盘亏{a['diff']:+d}")
                            adj_rows.append([code, a["name"],
                                             a.get("book_onhand", 0),
                                             a.get("actual_qty", 0),
                                             f"{a['diff']:+d}",
                                             "; ".join(dest)])
                        print_table(["编号", "名称", "账面在场", "实盘", "差异", "去向"], adj_rows)
                # 调账前后对比快照
                if s.get("adjustments_made") and s.get("pre_adjust_snapshot") and s.get("adjustments"):
                    print(f"\n📊 调账前后对比 (调前快照→调后实际)")
                    cmp_rows = []
                    pre = s["pre_adjust_snapshot"]
                    adj = s["adjustments"]
                    # 获取调后实际库存(从inventory.json重查)
                    inv_cur = get_inventory()
                    for code, a in adj.items():
                        p = pre.get(code, {})
                        cur = inv_cur["items"].get(code, {})
                        pre_s = f"总{p.get('qty_total','?')} 库{p.get('qty_available','?')} 维{p.get('qty_repair','?')} 报{p.get('qty_scrapped','?')}"
                        if cur:
                            post_s = f"总{cur.get('qty_total','?')} 库{cur.get('qty_available','?')} 维{cur.get('qty_repair','?')} 报{cur.get('qty_scrapped','?')}"
                        else:
                            post_s = "(盘盈新增) 总" + str(a.get("actual_qty", 0))
                        cmp_rows.append([code, a["name"][:14], pre_s, post_s])
                    print_table(["编号", "名称", "调前(总/库/维/报)", "调后(总/库/维/报)"], cmp_rows[:50])
                if s.get("adjust_confirmed_by"):
                    print(f"📌 调账确认: {s['adjust_confirmed_by']} @ {s.get('adjust_confirmed_at', '')}")
                # 差异回看
                print(f"\n🔍 差异回看 (会话 {s['id']})")
                fake_args = argparse.Namespace(id=s["id"], limit=args.limit)
                cmd_count_diff(fake_args)
                return
        print("❌ 会话不存在")
        return
    sessions = sessions[-args.limit:] if args.limit else sessions
    print(f"\n📋 盘点会话列表 (共{len(cs_obj['sessions'])}次)")
    headers = ["会话ID", "库位", "阶段", "状态", "操作人", "开始时间", "账面/实盘/复核", "复核人", "已调整"]
    rows = []
    for s in sessions:
        n_book = len(s.get("book_snapshot", {}))
        n_cnt = len(s.get("counts", {}))
        n_rev = len(s.get("review_counts", {}))
        rows.append([s["id"], s.get("location", "ALL"), s.get("stage", "-"), s["status"],
                     s["operator"], s["started_at"],
                     f"{n_book}/{n_cnt}/{n_rev}",
                     s.get("reviewer", "-"),
                     "是" if s.get("adjustments_made") else ""])
    print_table(headers, rows)


def cmd_dunning(args):
    """借用人催还清单 - 三责任人分离 + 筛选"""
    cfg = get_config()
    borrow_db = get_borrow_records()
    br = borrow_db["records"]
    overdue_days = cfg.get("overdue_days", 30)
    today = datetime.now().date()

    # 筛选参数
    filter_overdue = getattr(args, "overdue_only", False)
    filter_missing_contact = getattr(args, "missing_contact", False)
    filter_borrower = getattr(args, "borrower", None)
    filter_limit = getattr(args, "dunning_limit", None)

    by_person = defaultdict(lambda: {
        "records": [],  # [br_id, code, name, qty, out, due, purpose, borrow_handler, approver, last_op, contact, od, appr, follow_status, follow_cnt, promised, last_follow]
        "qty": 0, "overdue_qty": 0, "recs": 0, "overdue_recs": 0,
        "contact": "", "borrow_handler": "", "approver": "", "last_op": "",
        "followup_cnt_total": 0, "has_pending": 0,
        "earliest_promised": "", "latest_follow": ""
    })

    for rec in br:
        if rec["status"] in ("closed", "rejected"): continue
        if rec.get("approval_status") == "rejected": continue
        out = rec["qty"] - rec["qty_returned"]
        if out <= 0: continue

        # 计算逾期
        od = 0
        if rec["due_date"]:
            try:
                due = datetime.strptime(rec["due_date"], "%Y-%m-%d").date()
                if today > due:
                    od = (today - due).days
                    if rec["status"] in ("active", "partial"):
                        rec["status"] = "overdue"
            except ValueError: pass

        # 三责任人分离
        borrow_handler = rec.get("handler", "")  # 借出登记时的经手人
        approver = ""                            # 审批人(history中approve的operator)
        last_operator = ""                       # 最后操作人
        last_op_time = ""
        for h in rec.get("history", []):
            op = h.get("operator", "")
            tm = h.get("time", "")
            act = h.get("action", "")
            if act == "approve" and op:
                approver = op
            if not last_op_time or tm > last_op_time:
                last_op_time = tm
                last_operator = op or borrow_handler
        if not last_operator:
            last_operator = rec.get("handler", "")

        # 跟进记录(需求4)
        followups = rec.get("followups", [])
        follow_cnt = len(followups)
        follow_status = "未跟进"
        promised = ""
        last_follow = ""
        if followups:
            last_fu = followups[-1]
            follow_status = last_fu.get("status", "已跟进")
            promised = last_fu.get("promised_date", "")
            last_follow = last_fu.get("time", "")[:16]

        # 按借用人级别汇总信息
        person = rec["borrower"]
        d = by_person[person]
        rec_data = [
            rec["id"], rec["code"], rec["toy_name"], rec["qty"], out,
            rec["due_date"], rec.get("purpose", ""), borrow_handler, approver,
            last_operator, rec.get("contact", ""), od,
            rec.get("approval_status", "approved"),
            follow_status, follow_cnt, promised, last_follow
        ]
        d["records"].append(rec_data)
        d["qty"] += out
        d["recs"] += 1
        if od > 0:
            d["overdue_qty"] += out
            d["overdue_recs"] += 1
        if rec.get("contact") and not d["contact"]:
            d["contact"] = rec["contact"]
        if borrow_handler and not d["borrow_handler"]:
            d["borrow_handler"] = borrow_handler
        if approver and not d["approver"]:
            d["approver"] = approver
        if last_operator:
            d["last_op"] = last_operator
        if follow_cnt:
            d["followup_cnt_total"] += follow_cnt
            if follow_status not in ("已处理", "已归还", "无需催还"):
                d["has_pending"] += 1
            if promised:
                if not d["earliest_promised"] or promised < d["earliest_promised"]:
                    d["earliest_promised"] = promised
            if last_follow:
                if not d["latest_follow"] or last_follow > d["latest_follow"]:
                    d["latest_follow"] = last_follow

    save_borrow_records(borrow_db)

    # === 应用筛选 ===
    filtered = {}
    for p, info in by_person.items():
        if filter_borrower and filter_borrower not in p: continue
        if filter_overdue and info["overdue_qty"] <= 0: continue
        if filter_missing_contact and info["contact"]: continue
        filtered[p] = info
    by_person = filtered

    # === 排序 + 取前N ===
    sorted_people = sorted(by_person.keys(),
                          key=lambda x: (-by_person[x]["overdue_qty"],
                                         -by_person[x]["qty"]))
    if filter_limit: sorted_people = sorted_people[:filter_limit]

    print(f"\n📞 借用人催还清单 (逾期阈值: {overdue_days}天)")
    filters = []
    if filter_overdue: filters.append("仅逾期")
    if filter_missing_contact: filters.append("联系方式缺失")
    if filter_borrower: filters.append(f"借用人含'{filter_borrower}'")
    if filter_limit: filters.append(f"前{filter_limit}人")
    if filters: print(f"  筛选条件: {', '.join(filters)}")
    print(f"  共 {len(sorted_people)} 位借用人, "
          f"{sum(by_person[p]['qty'] for p in sorted_people)} 件未归还, 其中逾期 "
          f"{sum(by_person[p]['overdue_qty'] for p in sorted_people)} 件")
    if not sorted_people:
        print("  ✅ 符合条件的借用人为空")
        return

    # === 汇总表 ===
    sum_rows = []
    for p in sorted_people:
        info = by_person[p]
        note = []
        if info["overdue_qty"] > 0: note.append("🔥逾期")
        if not info["contact"]: note.append("⚠缺联系方式")
        if info["followup_cnt_total"] > 0:
            note.append(f"已跟进{info['followup_cnt_total']}次")
        if info["has_pending"] > 0 and info["followup_cnt_total"] > 0:
            note.append(f"{info['has_pending']}笔待归还")
        if info["earliest_promised"]:
            note.append(f"承诺最早{info['earliest_promised']}")
        sum_rows.append([
            p, info["recs"], info["qty"], info["overdue_recs"], info["overdue_qty"],
            info["borrow_handler"] or "-", info["approver"] or "-", info["last_op"] or "-",
            info["contact"] or "-",
            f"未跟进" if info["followup_cnt_total"] == 0 else (f"{info['followup_cnt_total']}次/待{info['has_pending']}笔"),
            info["earliest_promised"] or "-",
            "; ".join(note)
        ])
    print(f"\n📊 催还责任汇总 (三责任人分离+跟进状态)")
    print_table(["借用人", "笔数", "未还", "逾期笔", "逾期件",
                "借出经手人★", "审批人", "最后操作人", "联系方式",
                "跟进", "承诺日期", "备注"], sum_rows)

    # === 逐人明细 ===
    print(f"\n📋 催还明细 (按借用人分组)")
    for p in sorted_people:
        info = by_person[p]
        print(f"\n  👤 {p}  —  "
              f"未还{info['qty']}件(逾期{info['overdue_qty']}件)  "
              f"借出:{info['borrow_handler'] or '-'} / 审批:{info['approver'] or '-'} / 最后操作:{info['last_op'] or '-'}  "
              f"联系方式:{info['contact'] or '⚠缺失'}")
        headers = ["单号", "编号", "玩具名称", "借出", "未还", "到期日", "逾期天",
                  "借出经手人", "审批人", "用途", "审批状态",
                  "跟进状态", "跟进次", "承诺日期", "最后跟进"]
        rows = []
        for rd in info["records"]:
            (rid, code, name, qty, out, due, purpose, bh, ap, lop, contact, od, appr,
             fs, fc, prm, lf) = rd
            rows.append([rid, code, name[:16], qty, out, due or "-",
                        od if od > 0 else "-",
                        bh[:8] or "-", ap[:8] or "-",
                        (purpose or "-")[:10], appr,
                        fs, fc, prm or "-", lf or "-"])
        print_table(headers, rows)

    print(f"\n💡 使用说明:")
    print(f"  ★ 借出经手人: 登记借出的老师, 优先联系确认借用目的")
    print(f"  筛选参数: --overdue-only / --missing-contact / --borrower <关键字> / --dunning-limit <N>")
    print(f"  登记催跟进: scan followup --br-id BRxxxx --followup-status 已联系 --promised-date 2026-06-20")


def cmd_scan_followup(args):
    """scan followup 登记催跟进"""
    borrow_db = get_borrow_records()
    br = borrow_db["records"]
    cfg = get_config()
    operator = args.operator or cfg.get("default_operator", "系统")

    # 定位借出单
    targets = []
    if getattr(args, "br_id", None):
        targets = [r for r in br if r["id"] == args.br_id]
    elif getattr(args, "borrower", None):
        kw = args.borrower
        for r in br:
            if r["status"] in ("closed", "rejected"): continue
            if r.get("approval_status") == "rejected": continue
            out = r["qty"] - r["qty_returned"]
            if out <= 0: continue
            if kw in r["borrower"]:
                targets.append(r)
    elif getattr(args, "codes", None):
        for code in args.codes:
            for r in br:
                if r["code"] != code: continue
                if r["status"] in ("closed", "rejected"): continue
                if r.get("approval_status") == "rejected": continue
                out = r["qty"] - r["qty_returned"]
                if out <= 0: continue
                targets.append(r)

    if not targets:
        print("❌ 未找到符合条件的借出单 (指定--br-id / --borrower / --codes)")
        return

    if not getattr(args, "followup_status", None):
        print("❌ 请指定 --followup-status (已联系/承诺归还/暂缓/已处理/无需催还)")
        return

    fu_status = args.followup_status
    promised = args.promised_date or ""
    note = getattr(args, "followup_note", None) or ""
    reason = args.reason or ""

    success = []
    for r in targets:
        # 写入followups数组
        if "followups" not in r:
            r["followups"] = []
        entry = {
            "time": now_str(),
            "operator": operator,
            "status": fu_status,
            "promised_date": promised,
            "reason": reason,
            "note": note
        }
        r["followups"].append(entry)

        # history同步留痕
        if "history" not in r:
            r["history"] = []
        detail_parts = [f"状态:{fu_status}"]
        if promised: detail_parts.append(f"承诺:{promised}")
        if reason: detail_parts.append(f"原因:{reason}")
        if note: detail_parts.append(f"备注:{note}")
        r["history"].append({
            "time": entry["time"], "action": "followup",
            "operator": operator, "detail": "; ".join(detail_parts)
        })
        success.append((r["id"], r["borrower"], r["code"], r["toy_name"],
                       r["qty"] - r["qty_returned"]))

    save_borrow_records(borrow_db)
    print(f"\n✅ 催跟进登记完成: {len(success)} 笔")
    headers = ["借出单号", "借用人", "编号", "玩具名称", "未还数"]
    print_table(headers, success)
    log_action("FOLLOWUP", f"状态={fu_status}, 目标{len(success)}笔, "
               f"承诺{promised or '-'}, 原因{reason or '-'}", operator)


def cmd_export_count(args):
    """按会话导出盘点差异: CSV + TXT"""
    # 找会话
    session = None
    if args.id:
        cs = get_count_sessions()
        session = next((s for s in cs["sessions"] if s["id"] == args.id), None)
    else:
        _cs, session = find_active_session()
    if not session:
        print("❌ 未找到指定会话(指定--id或存在活跃会话)")
        return

    res = analyze_diff(session)
    sid = session["id"]
    out_prefix = args.output or f"count_{sid}"
    do_csv = args.format in ("csv", "all")
    do_txt = args.format in ("txt", "all")

    # ===== CSV: 完整差异表 (初盘/复核/最终 三栏) =====
    if do_csv:
        csv_file = f"{out_prefix}.csv"
        with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["盘点差异表", f"会话: {sid}", f"库位: {session.get('location', 'ALL')}"])
            w.writerow(["操作人", session["operator"], "开始", session["started_at"]])
            if session.get("reviewer"):
                w.writerow(["复核人", session["reviewer"], "复核时间", session.get("reviewed_at", "")])
            if session.get("finished_at"):
                w.writerow(["结束", session["finished_at"], "已调账", "是" if session.get("adjustments_made") else "否"])
            w.writerow([])
            w.writerow(["汇总"])
            w.writerow(["账面品种", "账面件数", "实盘品种", "实盘件数",
                       "相符", "复核后相符", "待复核差异", "复核后差异",
                       "未盘到", "确认未盘到", "待复核盘盈", "复核后盘盈"])
            s = res["summary"]
            w.writerow([s["book_types"], s["book_qty"], s["actual_types"], s["actual_qty"],
                       s["matched"], s["matched_reviewed"], s["diff_pending"], s["diff_reviewed"],
                       s["missing_pending"], s["missing_confirmed"], s["extra_pending"], s["extra_reviewed"]])
            w.writerow([])
            w.writerow(["编号", "名称", "分类", "账面在库", "账面维修", "账面在场",
                       "初盘数量", "复核数量", "最终调账数", "差异", "状态/判定"])
            # 状态映射
            status_zh = {
                "matched": "相符", "matched_reviewed": "相符(复核确认)",
                "盘盈_pending": "盘盈", "盘盈_reviewed": "盘盈(复核确认)",
                "盘亏_pending": "盘亏", "盘亏_reviewed": "盘亏(复核确认)",
                "missing_pending": "未盘到", "missing_confirmed": "未盘到(确认盘亏)",
                "extra_pending": "盘盈(新,待复核)", "extra_reviewed": "盘盈(新,复核确认)"
            }
            for c in res["cols_all"]:
                st = status_zh.get(c["status"], c["status"])
                w.writerow([c["code"], c["name"], c.get("category", ""),
                           c["book_qa"], c["book_qr"], c["book_onhand"],
                           c["count_qty"] if c["count_qty"] is not None else "",
                           c["review_qty"] if c["review_qty"] is not None else "",
                           c["effective_qty"],
                           f"{c['diff']:+d}" if c["diff"] else 0, st])

            # 调账明细(如有)
            if session.get("adjustments_made") and session.get("adjustments"):
                w.writerow([])
                w.writerow(["调账明细"])
                w.writerow(["编号", "名称", "账面在场", "实盘", "差异", "报废(来自维修)", "报废(来自在库)", "新增盘盈(在库)"])
                for code, a in session["adjustments"].items():
                    w.writerow([code, a["name"], a.get("book_onhand", 0), a.get("actual_qty", 0),
                               a["diff"],
                               a.get("from_repair", 0), a.get("from_available", 0),
                               a.get("to_available", 0)])
        print(f"📄 盘点差异 CSV -> {csv_file}")

    # ===== TXT: 人可读版差异表 =====
    if do_txt:
        txt_file = f"{out_prefix}.txt"
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("=" * 72 + "\n")
            f.write(f"  玩具仓库盘点差异报告  会话: {sid}\n")
            f.write("=" * 72 + "\n")
            f.write(f"库位: {session.get('location', 'ALL')}    操作人: {session['operator']}    开始: {session['started_at']}\n")
            f.write(f"阶段: {session.get('stage', '-')}    状态: {session['status']}\n")
            if session.get("reviewer"):
                f.write(f"复核人: {session['reviewer']}    复核时间: {session.get('reviewed_at', '-')}\n")
            if session.get("finished_at"):
                f.write(f"结束: {session['finished_at']}    已调账: {'是' if session.get('adjustments_made') else '否'}\n")
            if session.get("note"):
                f.write(f"备注: {session['note']}\n")
            s = res["summary"]
            f.write("\n【一、汇总】\n")
            f.write(f"  账面在场 {s['book_types']} 品种 / {s['book_qty']} 件\n")
            f.write(f"  实盘在场 {s['actual_types']} 品种 / {s['actual_qty']} 件\n")
            f.write(f"  ✔ 相符: {s['matched']}    ✔复核后相符: {s['matched_reviewed']}\n")
            f.write(f"  ⚠ 待复核差异: {s['diff_pending']}    ✔已复核差异: {s['diff_reviewed']}\n")
            f.write(f"  ❌ 未盘到: {s['missing_pending']}    ❌确认未盘到: {s['missing_confirmed']}\n")
            f.write(f"  ➕ 待复核盘盈: {s['extra_pending']}    ✔已复核盘盈: {s['extra_reviewed']}\n")

            f.write("\n【二、逐品种明细 (初盘/复核/最终)】\n")
            f.write("  " + "-" * 90 + "\n")
            f.write(f"  {'编号':<7}{'名称':<18}{'账面在库+维修':>12}{'账面在场':>8}{'初盘':>6}{'复核':>6}{'最终':>6}{'差异':>8}{'状态':<18}\n")
            f.write("  " + "-" * 90 + "\n")
            status_zh2 = {
                "matched": "相符", "matched_reviewed": "相符(复核确认)",
                "盘盈_pending": "盘盈(待复核)", "盘盈_reviewed": "盘盈(复核确认)",
                "盘亏_pending": "盘亏(待复核)", "盘亏_reviewed": "盘亏(复核确认)",
                "missing_pending": "未盘到(待复核)", "missing_confirmed": "未盘到(确认盘亏)",
                "extra_pending": "盘盈(新,待复核)", "extra_reviewed": "盘盈(新,复核确认)"
            }
            for c in res["cols_all"]:
                st = status_zh2.get(c["status"], c["status"])
                bk = f"{c['book_qa']}+{c['book_qr']}"
                cq = str(c["count_qty"]) if c["count_qty"] is not None else "-"
                rq = str(c["review_qty"]) if c["review_qty"] is not None else "-"
                df = f"{c['diff']:+d}"
                f.write(f"  {c['code']:<7}{c['name'][:18]:<18}{bk:>12}{c['book_onhand']:>8}{cq:>6}{rq:>6}{c['effective_qty']:>6}{df:>8}{st:<18}\n")

            # 调账明细
            if session.get("adjustments_made") and session.get("adjustments"):
                f.write(f"\n【三、调账明细 ({session.get('adjust_count', 0)} 品种, 净 {session.get('adjust_qty_net', 0):+d} 件)】\n")
                f.write("  " + "-" * 80 + "\n")
                f.write(f"  {'编号':<7}{'名称':<18}{'账面':>6}{'实盘':>6}{'差异':>8}  {'去向说明':<30}\n")
                f.write("  " + "-" * 80 + "\n")
                for code, a in session["adjustments"].items():
                    diff_str = f"{a['diff']:+d}"
                    dest = []
                    if a.get("from_repair", 0): dest.append(f"维修池→报废{a['from_repair']}")
                    if a.get("from_available", 0): dest.append(f"在库→报废{a['from_available']}")
                    if a.get("to_available", 0): dest.append(f"盘盈→在库{a['to_available']}")
                    f.write(f"  {code:<7}{a['name'][:18]:<18}{a.get('book_onhand', 0):>6}{a.get('actual_qty', 0):>6}{diff_str:>8}  {'; '.join(dest):<30}\n")

            f.write("\n" + "=" * 72 + "\n")
            f.write("              差异报告结束  —  请负责人审阅签字\n")
            f.write("=" * 72 + "\n")
        print(f"📄 盘点差异 TXT -> {txt_file}")

    log_action("COUNT-EXPORT", f"导出盘点差异表 会话={sid} 格式={args.format}")
    print(f"\n✅ 盘点差异表导出完成！可直接发送给负责人复核。")


def cmd_count_trend(args):
    """库位盘点趋势: 最近N次差异变化 + 问题玩具排行"""
    cs = get_count_sessions()
    loc = args.location
    n = args.limit
    top_n = args.top

    sessions = [s for s in cs["sessions"]
                if s.get("location") == loc and s.get("status") == "closed"]
    sessions.sort(key=lambda s: s["started_at"], reverse=True)
    sessions = sessions[:n]

    if not sessions:
        print(f"❌ 库位 {loc} 尚无已完成的盘点会话")
        return

    print(f"\n📈 库位 {loc} 盘点趋势分析 (最近 {len(sessions)} 次盘点, TOP{top_n} 问题玩具)")
    sessions.reverse()  # 倒回正序(由旧→新)

    # 每次会话的汇总 + 逐玩具差异
    trend_rows = []
    code_stats = defaultdict(lambda: {
        "name": "", "total_diff_abs": 0, "total_loss": 0, "total_gain": 0,
        "times_diff": 0, "times_missing": 0,
        "history": []  # [(date_str, diff_str, status)]
    })

    for s in sessions:
        res = analyze_diff(s)
        sm = res["summary"]
        net_diff = sm["actual_qty"] - sm["book_qty"]
        loss_abs = sum(abs(c["diff"]) for c in res["cols_all"] if c["diff"] < 0)
        gain_abs = sum(c["diff"] for c in res["cols_all"] if c["diff"] > 0)
        diff_types = (sm["diff_pending"] + sm["diff_reviewed"] +
                      sm["missing_pending"] + sm["missing_confirmed"] +
                      sm["extra_pending"] + sm["extra_reviewed"])
        trend_rows.append([s["id"], s["started_at"][:16],
                          sm["book_types"], sm["book_qty"],
                          sm["actual_types"], sm["actual_qty"],
                          f"{net_diff:+d}",
                          sm["matched_reviewed"] + sm["matched"],
                          diff_types, loss_abs, gain_abs,
                          s.get("reviewer", "-"),
                          "是" if s.get("adjustments_made") else ""])

        # 统计逐玩具
        for c in res["cols_all"]:
            if c["diff"] == 0: continue
            st = code_stats[c["code"]]
            st["name"] = c["name"]
            st["total_diff_abs"] += abs(c["diff"])
            if c["diff"] < 0:
                st["total_loss"] += abs(c["diff"])
            else:
                st["total_gain"] += c["diff"]
            st["times_diff"] += 1
            if "missing" in c.get("status", ""):
                st["times_missing"] += 1
            tag = {
                "matched": "相符", "matched_reviewed": "相符(复)",
                "盘盈_pending": "盘盈", "盘盈_reviewed": "盘盈(复)",
                "盘亏_pending": "盘亏", "盘亏_reviewed": "盘亏(复)",
                "missing_pending": "未盘", "missing_confirmed": "未盘(亏)",
                "extra_pending": "盘盈新", "extra_reviewed": "盘盈新(复)"
            }.get(c.get("status", ""), c.get("status", ""))
            st["history"].append(f"{s['started_at'][5:10]}{c['diff']:+d}({tag})")

    # 会话趋势表
    print(f"\n【一、会话级趋势 (旧→新)】")
    print_table(["会话ID", "开始时间", "账面(种/件)", "实盘(种/件)", "净差",
                 "相符", "差异种", "盘亏件", "盘盈件", "复核人", "调账"],
                [[r[0], r[1], f"{r[2]}/{r[3]}", f"{r[4]}/{r[5]}", r[6],
                  r[7], r[8], r[9], r[10], r[11], r[12]] for r in trend_rows])

    # 问题玩具排行
    ranked = sorted(code_stats.items(),
                   key=lambda kv: (-kv[1]["total_diff_abs"], -kv[1]["times_diff"]))[:top_n]
    print(f"\n【二、问题玩具 TOP{min(top_n, len(ranked))} (按累计差异绝对值)】")
    rank_rows = []
    for code, st in ranked:
        rank_rows.append([code, st["name"][:18],
                         st["times_diff"], st["times_missing"],
                         st["total_loss"], st["total_gain"], st["total_diff_abs"],
                         " | ".join(st["history"][-6:])])
    print_table(["编号", "名称", "出差异次", "未盘到次",
                 "累计盘亏", "累计盘盈", "累计绝对差", "最近6次变化(月-日 差异(状态))"],
                rank_rows)

    # 建议
    if ranked:
        bad_codes = [f"{c}(差{st['times_diff']}次)" for c, st in ranked[:3] if st["times_diff"] >= 2]
        if bad_codes:
            print(f"\n💡 建议: 反复出问题的玩具 {'; '.join(bad_codes)}, "
                  f"可考虑加强交接管理或调整存放位置。")

    # 可选导出CSV
    if args.export:
        csv_path = args.export if args.export.endswith(".csv") else f"{args.export}.csv"
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow([f"库位{loc}盘点趋势", f"回看{len(sessions)}次", f"生成:{now_str()}"])
            w.writerow([])
            w.writerow(["会话级趋势"])
            w.writerow(["会话ID", "开始时间", "账面品种", "账面件数",
                       "实盘品种", "实盘件数", "净差异",
                       "相符品种", "差异品种", "盘亏件数", "盘盈件数",
                       "复核人", "是否调账"])
            for r in trend_rows:
                w.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6],
                           r[7], r[8], r[9], r[10], r[11], r[12]])
            w.writerow([])
            w.writerow([f"问题玩具TOP{top_n}"])
            w.writerow(["编号", "名称", "出差异次", "未盘到次",
                       "累计盘亏", "累计盘盈", "累计绝对差", "最近变化"])
            for code, st in ranked:
                w.writerow([code, st["name"], st["times_diff"], st["times_missing"],
                           st["total_loss"], st["total_gain"], st["total_diff_abs"],
                           " | ".join(st["history"])])
        log_action("COUNT-TREND", f"库位{loc}趋势导出 -> {csv_path}")
        print(f"\n📄 趋势CSV -> {csv_path}")

    log_action("COUNT-TREND", f"库位{loc}趋势分析, 回看{len(sessions)}次")


# ==================== check 命令 ====================

def cmd_check(args):
    if hasattr(args, "count_cmd") and args.count_cmd:
        sub = args.count_cmd
        if sub == "start":
            cmd_count_start(args)
        elif sub == "scan":
            cmd_count_scan(args)
        elif sub == "diff":
            cmd_count_diff(args)
        elif sub == "review":
            cmd_count_review(args)
        elif sub == "close":
            cmd_count_close(args)
        elif sub == "list":
            cmd_count_list(args)
        elif sub == "export":
            cmd_export_count(args)
        elif sub == "trend":
            cmd_count_trend(args)
        elif sub == "abort":
            cs_obj, s = find_active_session()
            if s:
                if not args.yes:
                    ans = input(f"确认放弃盘点会话 [{s['id']}]? [y/N]: ").strip().lower()
                    if ans != "y":
                        print("已取消")
                        return
                s["status"] = "closed"
                s["finished_at"] = now_str()
                s["aborted"] = True
                cs_obj["active_id"] = None
                save_count_sessions(cs_obj)
                log_action("COUNT-ABORT", f"放弃盘点会话{s['id']}")
                print(f"✅ 已放弃盘点会话 [{s['id']}]")
            else:
                print("❌ 没有进行中的盘点会话")
        return

    cfg = get_config()
    inv = get_inventory()
    items = inv["items"]
    borrow_db = get_borrow_records()
    borrow_list = borrow_db["records"]

    if args.overdue:
        overdue_days = cfg.get("overdue_days", 30)
        today = datetime.now().date()
        overdue_items = []
        for rec in borrow_list:
            if rec["status"] not in ("active", "partial", "overdue"):
                continue
            if not rec["due_date"]:
                continue
            try:
                due = datetime.strptime(rec["due_date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            if today > due:
                days = (today - due).days
                outstanding = rec["qty"] - rec["qty_returned"]
                overdue_items.append([
                    rec["id"], rec["code"], rec["toy_name"], rec["borrower"],
                    outstanding, rec["borrow_date"], rec["due_date"], f"{days}天"
                ])
                if rec["status"] in ("active", "partial"):
                    rec["status"] = "overdue"
        save_borrow_records(borrow_db)

        print(f"\n⚠ 逾期未还清单 (阈值:{overdue_days}天)  共{len(overdue_items)}笔借出单")
        by_person = defaultdict(lambda: {"records": [], "qty": 0})
        for r in overdue_items:
            by_person[r[3]]["records"].append(r)
            by_person[r[3]]["qty"] += r[4]
        print(f"  涉及借用人: {len(by_person)} 人")
        if by_person:
            print("  逾期责任人汇总:")
            for p, info in sorted(by_person.items()):
                print(f"    • {p}: {info['qty']}件 / {len(info['records'])}笔")
        headers = ["借出单号", "编号", "玩具名称", "借用人", "未还数", "借出日", "到期日", "逾期天数"]
        print_table(headers, overdue_items)
        return

    if args.dunning:
        cmd_dunning(args)
        return

    if args.all:
        print("\n📋 全面盘点 - 细分数量口径")
        ta = tb = tr = ts = 0
        headers = ["编号", "名称", "分类", "库位", "总库存", "在库", "借出", "维修", "报废", "状态"]
        rows = []
        for code, item in sorted(items.items()):
            qa = item.get("qty_available", 0)
            qb = item.get("qty_borrowed", 0)
            qr = item.get("qty_repair", 0)
            qs = item.get("qty_scrapped", 0)
            qt = item.get("qty_total", 0)
            rows.append([code, item["name"], item["category"], item["location"], qt, qa, qb, qr, qs, item["status"]])
            ta += qa; tb += qb; tr += qr; ts += qs
        print_table(headers, rows)
        print(f"\n合计: {len(rows)}个品种  在库{ta}  借出{tb}  维修{tr}  报废{ts}")
        return

    if args.location:
        loc = args.location
        if loc not in cfg["locations"]:
            print(f"❌ 库位 {loc} 不存在")
            return
        loc_items = []
        la = lb = lr = ls = 0
        headers = ["编号", "名称", "分类", "货架", "在库", "借出", "维修", "报废", "口径"]
        for code, item in items.items():
            if item["location"] == loc:
                qa = item.get("qty_available", 0)
                qb = item.get("qty_borrowed", 0)
                qr = item.get("qty_repair", 0)
                qs = item.get("qty_scrapped", 0)
                loc_items.append([code, item["name"], item["category"], item["shelf"], qa, qb, qr, qs, short_status_text(item)])
                la += qa; lb += qb; lr += qr; ls += qs
        print(f"\n📦 按库位盘点: {loc} - {cfg['locations'][loc]['description']}")
        print(f"   货架: {', '.join(cfg['locations'][loc]['shelves'])}  容量: {cfg['locations'][loc]['capacity']}")
        print_table(headers, loc_items)
        print(f"合计: {len(loc_items)}个品种  在库{la}  借出{lb}  维修{lr}  报废{ls}")
        return

    if args.borrowed:
        print("\n📤 借出中明细 (按借用人分组)")
        by_person = defaultdict(list)
        total_out = total_rec = 0
        for rec in borrow_list:
            if rec["status"] in ("active", "partial", "overdue"):
                by_person[rec["borrower"]].append(rec)
                total_out += (rec["qty"] - rec["qty_returned"])
                total_rec += 1
        if not by_person:
            print("(无借出记录)")
            return
        print(f"  共 {len(by_person)} 位借用人, {total_rec} 笔借出单, 合计 {total_out} 件在外")

        for person in sorted(by_person.keys()):
            recs = by_person[person]
            pq = sum(r["qty"] - r["qty_returned"] for r in recs)
            print(f"\n  👤 {person}  ({len(recs)}笔, 未还{pq}件)")
            headers = ["借出单号", "编号", "玩具名称", "借出", "已还", "未还", "借出日", "到期日", "续借", "状态"]
            rows = [[r["id"], r["code"], r["toy_name"], r["qty"], r["qty_returned"],
                     r["qty"] - r["qty_returned"], r["borrow_date"], r["due_date"],
                     r.get("renew_count", 0), r["status"]] for r in recs]
            print_table(headers, rows)
        return

    if args.borrower:
        person = args.borrower
        recs = [r for r in borrow_list if r["borrower"] == person and r["status"] != "closed"]
        if not recs:
            print(f"❌ 未找到 {person} 的未结清借出记录")
            return
        print(f"\n📤 {person} 的未结清借出:")
        headers = ["借出单号", "编号", "玩具名称", "借出", "已还", "未还", "借出日", "到期日", "状态"]
        rows = [[r["id"], r["code"], r["toy_name"], r["qty"], r["qty_returned"],
                 r["qty"] - r["qty_returned"], r["borrow_date"], r["due_date"], r["status"]] for r in recs]
        print_table(headers, rows)
        return

    print("请指定盘点方式:")
    print("  --location <库位>   按库位盘点")
    print("  --all               全部盘点(细分数量)")
    print("  --borrowed          借出中(按借用人分组)")
    print("  --borrower <姓名>   指定借用人明细")
    print("  --overdue           逾期未还清单")
    print("\n盘点会话 (check count-*):")
    print("  count-start [--location X]  开启会话")
    print("  count-scan  <编号数量...>   录入实盘")
    print("  count-diff                  查看差异")
    print("  count-close [--apply]       结束并可选调账")
    print("  count-list                  历史会话")


# ==================== report 命令 ====================

def cmd_report(args):
    inv = get_inventory()
    items = inv["items"]
    borrow_db = get_borrow_records()
    borrow_list = borrow_db["records"]

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
        print("\n🔍 盘点差异清单 (账面对比)")
        print(f"  台账应有: {len(expected_codes)} 个品种")
        print(f"  实际存在: {len(actual_codes)} 个品种")
        if missing:
            print(f"\n❌ 缺少 (台账有但库中无): {len(missing)} 件")
            print_table(["编号", "台账状态"], [[c, "缺失"] for c in sorted(missing)])
        if extra:
            print(f"\n⚠ 多余 (库中有但台账无): {len(extra)} 件")
            rows = [[c, items[c]["name"], items[c]["location"], short_status_text(items[c])] for c in sorted(extra)]
            print_table(["编号", "名称", "库位", "细分数量"], rows)
        if not missing and not extra and expected_codes:
            print("\n✅ 账实相符，无差异！")
        return

    if args.type == "age":
        today = datetime.now().date()
        buckets = {"0-30天": [], "31-90天": [], "91-180天": [], "181-365天": [], "365天以上": []}
        for code, item in items.items():
            if not item["inbound_date"]:
                continue
            try:
                in_date = datetime.strptime(item["inbound_date"], "%Y-%m-%d").date()
                days = (today - in_date).days
                qa = item.get("qty_available", 0)
                qb = item.get("qty_borrowed", 0)
                qr = item.get("qty_repair", 0)
                total = qa + qb + qr
                if total <= 0:
                    continue
                entry = [code, item["name"], item["category"], item["inbound_date"], f"{days}天", qa, qb, qr, total]
                if days <= 30: buckets["0-30天"].append(entry)
                elif days <= 90: buckets["31-90天"].append(entry)
                elif days <= 180: buckets["91-180天"].append(entry)
                elif days <= 365: buckets["181-365天"].append(entry)
                else: buckets["365天以上"].append(entry)
            except ValueError:
                continue

        print("\n📊 库龄分析报告")
        headers = ["库龄区间", "品种数", "在库数", "借出数", "维修数", "总件数"]
        rows = []
        for b in ["0-30天", "31-90天", "91-180天", "181-365天", "365天以上"]:
            es = buckets[b]
            rows.append([b, len(es), sum(e[5] for e in es), sum(e[6] for e in es),
                        sum(e[7] for e in es), sum(e[8] for e in es)])
        print_table(headers, rows)
        if args.detail:
            for b in ["365天以上", "181-365天"]:
                if buckets[b]:
                    print(f"\n📌 {b} 明细 (呆滞库存建议关注):")
                    print_table(["编号", "名称", "分类", "入库日期", "库龄", "在库", "借出", "维修", "总数"],
                                [[e[0], e[1], e[2], e[3], e[4], e[5], e[6], e[7], e[8]]
                                 for e in buckets[b][:args.detail]])
        return

    if args.type == "category":
        cat_stats = {}
        for code, item in items.items():
            cat = item["category"]
            if cat not in cat_stats:
                cat_stats[cat] = {"types": 0, "total": 0, "available": 0, "borrowed": 0,
                                  "repair": 0, "scrapped": 0, "value": 0.0}
            s = cat_stats[cat]
            qa = item.get("qty_available", 0)
            qb = item.get("qty_borrowed", 0)
            qr = item.get("qty_repair", 0)
            qs = item.get("qty_scrapped", 0)
            s["types"] += 1
            s["available"] += qa
            s["borrowed"] += qb
            s["repair"] += qr
            s["scrapped"] += qs
            s["total"] += item.get("qty_total", 0)
            s["value"] += qa * item["price"]

        print("\n📊 分类统计报告 (⚠ 安全库存按 在库可用数 判断, 借出/维修不算可用)")
        headers = ["分类", "品种数", "总库存", "可用(在库)", "借出", "维修", "报废", "可用库存金额"]
        rows = []
        total = {"types": 0, "total": 0, "available": 0, "borrowed": 0, "repair": 0, "scrapped": 0, "value": 0.0}
        for cat in sorted(cat_stats.keys()):
            s = cat_stats[cat]
            rows.append([cat, s["types"], s["total"], s["available"], s["borrowed"], s["repair"],
                         s["scrapped"], f"¥{s['value']:.2f}"])
            for k in total: total[k] += s[k]
        rows.append(["合计", total["types"], total["total"], total["available"], total["borrowed"],
                     total["repair"], total["scrapped"], f"¥{total['value']:.2f}"])
        print_table(headers, rows)

        cfg = get_config()
        ss = cfg.get("safety_stock", {})
        low_stock = []
        for cat in cat_stats:
            threshold = ss.get(cat, ss.get("_default", 0))
            if threshold and cat_stats[cat]["available"] < threshold:
                low_stock.append([cat, cat_stats[cat]["available"], threshold,
                                  f"缺{threshold - cat_stats[cat]['available']}件"])
        if low_stock:
            print("\n🚨 安全库存预警 (按可用在库数比较):")
            print_table(["分类", "当前可用", "安全阈值", "缺口"], low_stock)
        else:
            print("\n✅ 所有分类的在库可用数均达到安全库存")
        return

    if args.type == "borrow":
        print("\n📒 借出明细台账 (含审批状态、用途、经手人、联系方式)")
        sf = args.status or "all"
        filtered = [r for r in borrow_list if sf == "all" or r["status"] == sf
                    or (sf == "overdue" and r.get("approval_status") == "pending")]
        if args.borrower:
            filtered = [r for r in filtered if r["borrower"] == args.borrower]
        print(f"  条件: 状态={sf}, 借用人={args.borrower or '全部'}, 共{len(filtered)}条")

        by_person = defaultdict(lambda: {"records": [], "total_out": 0, "total_qty": 0})
        for r in filtered:
            outstanding = r["qty"] - r["qty_returned"]
            if r["status"] in ("active", "partial", "overdue"):
                by_person[r["borrower"]]["records"].append(r)
                by_person[r["borrower"]]["total_out"] += outstanding
                by_person[r["borrower"]]["total_qty"] += r["qty"]

        headers = ["借出单号", "编号", "玩具名称", "借出人", "借出", "已还", "未还",
                   "借出日", "到期日", "续借", "审批", "用途", "经手人", "联系方式", "状态"]
        rows = []
        for r in filtered:
            rows.append([r["id"], r["code"], r["toy_name"], r["borrower"],
                         r["qty"], r["qty_returned"], r["qty"] - r["qty_returned"],
                         r["borrow_date"], r["due_date"], r.get("renew_count", 0),
                         r.get("approval_status", "approved"),
                         r.get("purpose", "")[:8] if r.get("purpose") else "",
                         r.get("handler", "")[:6] if r.get("handler") else "",
                         r.get("contact", "")[:10] if r.get("contact") else "",
                         r["status"]])
        print_table(headers, rows)

        if by_person:
            print(f"\n📌 借用人未还责任汇总 (共{len(by_person)}人):")
            sum_headers = ["借用人", "未结清笔数", "借出总量", "仍未还数"]
            sum_rows = []
            for person in sorted(by_person.keys()):
                info = by_person[person]
                sum_rows.append([person, len(info["records"]), info["total_qty"], info["total_out"]])
            print_table(sum_headers, sum_rows)

        if args.borrower:
            person = args.borrower
            his = [r for r in borrow_list if r["borrower"] == person]
            active = [r for r in his if r["status"] in ("active", "partial", "overdue")]
            for r in active:
                print(f"\n📜 [{r['id']}] {person} 借 {r['toy_name']}(x{r['qty']}) 完整流转记录:")
                for h in r.get("history", []):
                    print(f"  [{h.get('time','?')[:16]}] {h.get('operator','')} {h['action']}: {h['detail']}")
        return

    if args.type == "logs":
        logs = get_logs()
        records = logs["records"]
        if args.limit:
            records = records[-args.limit:]
        print(f"\n📝 操作日志 (最近 {len(records)} 条)")
        headers = ["时间", "操作人", "操作", "详情"]
        rows = [[r["timestamp"], r["operator"], r["action"],
                 (r["detail"][:60] + "...") if len(r["detail"]) > 60 else r["detail"]] for r in records]
        print_table(headers, rows)
        return

    print("请指定报告类型: diff / age / category / borrow / logs")


# ==================== label 命令 ====================

def cmd_label(args):
    cfg = get_config()
    inv = get_inventory()
    items = inv["items"]

    if args.safety:
        category = args.category or "_default"
        if args.list:
            print("\n🛡 当前安全库存设置 (按 在库可用数 预警):")
            print_table(["分类", "安全阈值"], [[k, v] for k, v in cfg.get("safety_stock", {}).items()])
            return
        threshold = args.threshold or 0
        if "safety_stock" not in cfg:
            cfg["safety_stock"] = {}
        cfg["safety_stock"][category] = threshold
        save_config(cfg)
        log_action("SAFETY-STOCK", f"设置 {category} 安全库存阈值={threshold}")
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
                "code": it["code"], "name": it["name"], "category": it["category"],
                "location": f"{it['location']}/{it['shelf']}", "price": it["price"],
                "qty_avail": it.get("qty_available", 0)
            })
        else:
            labels.append({"code": code, "name": "未登记", "category": "-", "location": "-", "price": 0, "qty_avail": 0})

    labels_text = ""
    per_row = args.cols or 2
    width = 40
    for i, lab in enumerate(labels):
        b = "+" + "-" * (width - 2) + "+"
        lines = [
            b,
            f"| 【{lab['code']}】" + " " * (width - len(lab['code']) - 8) + "|",
            f"| 名称: {lab['name'][:22]}" + " " * max(0, width - len(lab['name']) - 10) + "|",
            f"| 分类: {lab['category'][:18]}" + " " * max(0, width - len(lab['category']) - 8) + "|",
            f"| 库位: {lab['location'][:18]}" + " " * max(0, width - len(lab['location']) - 8) + "|",
            f"| 价格: ¥{lab['price']:.2f}   在库: {lab['qty_avail']}" + " " * max(0, width - 25 - len(str(lab['qty_avail']))) + "|",
            b
        ]
        labels_text += "\n".join(lines) + "\n"
        if (i + 1) % per_row == 0:
            labels_text += "\n"

    output_file = args.output or "labels.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(labels_text)
    log_action("LABEL", f"生成标签 {len(labels)} 张 -> {output_file}")
    print(f"🏷 已生成 {len(labels)} 张标签 -> {output_file}")
    print("\n标签预览 (前2张):")
    preview = labels_text.split("\n")[:7 * min(2, per_row) * 2]
    print("\n".join(preview))


# ==================== export 命令 ====================

def cmd_export(args):
    cfg = get_config()
    inv = get_inventory()
    items = inv["items"]
    logs = get_logs()
    borrow_db = get_borrow_records()
    borrow_list = borrow_db["records"]

    fmt = args.format.lower()
    output = args.output or f"inventory_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    if fmt == "csv" or fmt == "all":
        csv_file = output + ".csv"
        with open(csv_file, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "编号", "名称", "分类", "品牌", "适用年龄",
                "库位", "货架", "单价",
                "总库存", "在库可用", "借出中", "维修中", "已报废",
                "入库日期", "库龄(天)", "状况", "状态", "备注"
            ])
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
                    it["location"], it["shelf"], it["price"],
                    it.get("qty_total", 0), it.get("qty_available", 0),
                    it.get("qty_borrowed", 0), it.get("qty_repair", 0), it.get("qty_scrapped", 0),
                    it["inbound_date"], age_days, it.get("condition", ""),
                    it.get("status", ""), it.get("remarks", "")
                ])
        # 额外导出借出明细台账CSV
        borrow_csv = output + "_borrows.csv"
        with open(borrow_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "借出单号", "玩具编号", "玩具名称", "借出人",
                "借出数量", "已还数量", "未还数量",
                "借出日期", "应还日期", "原始应还", "续借次数", "审批状态",
                "用途", "经手人", "联系方式", "备注", "状态", "操作人"
            ])
            for r in borrow_list:
                writer.writerow([
                    r["id"], r["code"], r["toy_name"], r["borrower"],
                    r["qty"], r["qty_returned"], r["qty"] - r["qty_returned"],
                    r["borrow_date"], r["due_date"], r.get("original_due", ""),
                    r.get("renew_count", 0), r.get("approval_status", "approved"),
                    r.get("purpose", ""), r.get("handler", ""), r.get("contact", ""),
                    r.get("remarks", ""), r["status"], r.get("operator", "")
                ])
        print(f"📄 CSV盘点表 -> {csv_file}")
        print(f"📄 CSV借出台账 -> {borrow_csv}")

        # 催还清单CSV (含逾期/联系方式/最近经手人)
        dunning_csv = output + "_dunning.csv"
        overdue_days_cfg = cfg.get("overdue_days", 30)
        today_d = datetime.now().date()
        by_person_dun = defaultdict(lambda: {
            "qty": 0, "overdue_qty": 0, "recs": 0, "overdue_recs": 0,
            "contact": "", "handler": "", "latest_time": "", "records": []
        })
        for rec in borrow_list:
            if rec["status"] in ("closed", "rejected") or rec.get("approval_status") == "rejected": continue
            out = rec["qty"] - rec["qty_returned"]
            if out <= 0: continue
            od = 0
            if rec["due_date"]:
                try:
                    due_d = datetime.strptime(rec["due_date"], "%Y-%m-%d").date()
                    if today_d > due_d: od = (today_d - due_d).days
                except ValueError: pass
            last_h = rec.get("handler", "")
            last_t = rec.get("created_at", "")
            for h in rec.get("history", []):
                if h.get("operator"):
                    last_h = h["operator"]
                    last_t = h.get("time", last_t)
            p = rec["borrower"]
            di = by_person_dun[p]
            di["records"].append([rec["id"], rec["code"], rec["toy_name"], rec["qty"], out,
                                 rec["due_date"], od, rec.get("purpose", ""),
                                 rec.get("approval_status", "approved")])
            di["qty"] += out; di["recs"] += 1
            if od > 0:
                di["overdue_qty"] += out; di["overdue_recs"] += 1
            if rec.get("contact") and not di["contact"]:
                di["contact"] = rec["contact"]
            if not di["latest_time"] or last_t > di["latest_time"]:
                di["latest_time"] = last_t
                di["handler"] = last_h or rec.get("handler", "")
        with open(dunning_csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow([f"借用人催还清单 (逾期阈值{overdue_days_cfg}天)",
                        f"生成时间: {now_str()}",
                        f"共 {len(by_person_dun)} 位借用人, "
                        f"{sum(v['qty'] for v in by_person_dun.values())} 件未还"])
            w.writerow(["借用人", "未结笔数", "未还件数", "逾期笔数", "逾期件数",
                       "最近经手人", "联系方式", "说明"])
            for p in sorted(by_person_dun.keys(),
                          key=lambda x: (-by_person_dun[x]["overdue_qty"], -by_person_dun[x]["qty"])):
                info = by_person_dun[p]
                note = []
                if info["overdue_qty"] > 0: note.append("逾期需紧急联系")
                if not info["contact"]: note.append("联系方式缺失,请补填")
                w.writerow([p, info["recs"], info["qty"], info["overdue_recs"], info["overdue_qty"],
                           info["handler"], info["contact"], "；".join(note)])
            w.writerow([])
            w.writerow(["逐笔明细"])
            w.writerow(["借用人", "单号", "编号", "玩具名称", "借出", "未还",
                       "到期日", "逾期(天)", "用途", "审批"])
            for p in sorted(by_person_dun.keys(),
                          key=lambda x: (-by_person_dun[x]["overdue_qty"], -by_person_dun[x]["qty"])):
                for row in by_person_dun[p]["records"]:
                    w.writerow([p] + row)
        print(f"📄 CSV催还清单 -> {dunning_csv}")

    if fmt == "json" or fmt == "all":
        json_file = output + ".json"
        total_types = len(items)
        total_avail = sum(it.get("qty_available", 0) for it in items.values())
        total_borrowed = sum(it.get("qty_borrowed", 0) for it in items.values())
        total_repair = sum(it.get("qty_repair", 0) for it in items.values())
        total_scrapped = sum(it.get("qty_scrapped", 0) for it in items.values())
        total_onhand_value = sum(it.get("qty_available", 0) * it["price"] for it in items.values())

        cat_stats = {}
        for it in items.values():
            cat = it["category"]
            if cat not in cat_stats:
                cat_stats[cat] = {"types": 0, "available": 0, "borrowed": 0, "repair": 0}
            cat_stats[cat]["types"] += 1
            cat_stats[cat]["available"] += it.get("qty_available", 0)
            cat_stats[cat]["borrowed"] += it.get("qty_borrowed", 0)
            cat_stats[cat]["repair"] += it.get("qty_repair", 0)

        ss = cfg.get("safety_stock", {})
        safety_alerts = []
        for cat, stat in cat_stats.items():
            threshold = ss.get(cat, ss.get("_default", 0))
            if threshold and stat["available"] < threshold:
                safety_alerts.append({
                    "category": cat, "available": stat["available"],
                    "threshold": threshold, "gap": threshold - stat["available"]
                })

        today = datetime.now().date()
        overdue_list = []
        for r in borrow_list:
            if r["status"] not in ("active", "partial", "overdue") or not r["due_date"]:
                continue
            try:
                due = datetime.strptime(r["due_date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            if today > due:
                overdue_list.append({
                    "id": r["id"], "code": r["code"], "toy_name": r["toy_name"],
                    "borrower": r["borrower"], "outstanding": r["qty"] - r["qty_returned"],
                    "borrow_date": r["borrow_date"], "due_date": r["due_date"],
                    "overdue_days": (today - due).days
                })

        report = {
            "generated_at": now_str(),
            "operator": cfg.get("operator", "system"),
            "summary": {
                "total_types": total_types,
                "qty_available_total": total_avail,
                "qty_borrowed_total": total_borrowed,
                "qty_repair_total": total_repair,
                "qty_scrapped_total": total_scrapped,
                "onhand_inventory_value": round(total_onhand_value, 2),
                "total_locations": len(cfg["locations"]),
                "total_operations": len(logs["records"]),
                "active_borrow_records": sum(1 for r in borrow_list if r["status"] != "closed"),
                "overdue_count": len(overdue_list)
            },
            "safety_stock": cfg.get("safety_stock", {}),
            "safety_alerts": safety_alerts,
            "overdue_items": overdue_list,
            "category_stats": cat_stats,
            "items": list(items.values()),
            "locations": cfg["locations"],
            "borrow_records": borrow_list
        }
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"📄 JSON完整报告 -> {json_file}")

    if fmt == "txt" or fmt == "all":
        txt_file = output + ".txt"
        today = datetime.now()
        today_d = today.date()
        total_types = len(items)
        total_avail = sum(it.get("qty_available", 0) for it in items.values())
        total_borrowed = sum(it.get("qty_borrowed", 0) for it in items.values())
        total_repair = sum(it.get("qty_repair", 0) for it in items.values())
        total_scrapped = sum(it.get("qty_scrapped", 0) for it in items.values())
        total_qt = sum(it.get("qty_total", 0) for it in items.values())

        cat_stats = {}
        for it in items.values():
            cat = it["category"]
            if cat not in cat_stats:
                cat_stats[cat] = {"types": 0, "available": 0, "borrowed": 0, "repair": 0, "scrapped": 0}
            s = cat_stats[cat]
            s["types"] += 1
            s["available"] += it.get("qty_available", 0)
            s["borrowed"] += it.get("qty_borrowed", 0)
            s["repair"] += it.get("qty_repair", 0)
            s["scrapped"] += it.get("qty_scrapped", 0)

        overdue_list = []
        for r in borrow_list:
            if r["status"] not in ("active", "partial", "overdue") or not r["due_date"]:
                continue
            try:
                due = datetime.strptime(r["due_date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            if today_d > due:
                overdue_list.append((r["code"], r["toy_name"], r["borrower"], r["qty"] - r["qty_returned"], (today_d - due).days))

        by_person_outstanding = defaultdict(lambda: {"records": [], "qty": 0})
        for r in borrow_list:
            if r["status"] in ("active", "partial", "overdue") and r.get("approval_status", "approved") == "approved":
                out = r["qty"] - r["qty_returned"]
                if out > 0:
                    by_person_outstanding[r["borrower"]]["records"].append(
                        (r["id"], r["code"], r["toy_name"], r["qty"], r["qty_returned"], out, r["due_date"], r.get("purpose", ""), r.get("handler", ""), r.get("contact", "")))
                    by_person_outstanding[r["borrower"]]["qty"] += out

        ss = cfg.get("safety_stock", {})
        low_stock = []
        for cat, stat in cat_stats.items():
            threshold = ss.get(cat, ss.get("_default", 0))
            if threshold and stat["available"] < threshold:
                low_stock.append((cat, stat["available"], threshold))

        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("=" * 64 + "\n")
            f.write("         玩具仓库盘点报告 (V2 细分数量口径)\n")
            f.write(f"    生成时间: {now_str()}\n")
            f.write(f"    操作人:   {cfg.get('operator', 'system')}\n")
            f.write("=" * 64 + "\n\n")

            f.write("【一、总体概况】\n")
            f.write(f"  品种总数: {total_types}\n")
            f.write(f"  总库存(扣已报废): {total_qt}\n")
            f.write(f"  ├─ 在库可用: {total_avail}\n")
            f.write(f"  ├─ 借出在外: {total_borrowed}\n")
            f.write(f"  ├─ 维修中:   {total_repair}\n")
            f.write(f"  └─ 已报废:   {total_scrapped}\n")
            f.write(f"  库位总数: {len(cfg['locations'])}  累计操作: {len(logs['records'])} 次\n\n")

            f.write("【二、分类统计 (可用库存仅计在库, 借出/维修不算)】\n")
            f.write(f"  {'分类':<10}{'品种':>5}{'可用':>6}{'借出':>6}{'维修':>6}{'报废':>6}\n")
            f.write("  " + "-" * 48 + "\n")
            for cat in sorted(cat_stats.keys()):
                s = cat_stats[cat]
                f.write(f"  {cat:<10}{s['types']:>5}{s['available']:>6}{s['borrowed']:>6}{s['repair']:>6}{s['scrapped']:>6}\n")
            if low_stock:
                f.write("\n  ⚠ 安全库存预警 (按可用在库数):\n")
                for cat, av, th in low_stock:
                    f.write(f"    • {cat}: 当前{av}, 阈值{th}, 缺{th-av}件\n")
            f.write("\n")

            if overdue_list:
                f.write("【三、逾期未还预警】\n")
                by_p = defaultdict(int)
                for _, _, p, qty, _ in overdue_list:
                    by_p[p] += qty
                for p, q in sorted(by_p.items()):
                    f.write(f"  ⚠ {p}: 逾期共{q}件\n")
                f.write("\n  明细:\n")
                for code, name, p, qty, days in overdue_list:
                    f.write(f"    [{code}] {name} | {p} | 未还{qty}件 | 逾期{days}天\n")
                f.write("\n")

            f.write("【四、库位明细盘点】\n")
            for loc_code in sorted(cfg["locations"].keys()):
                loc = cfg["locations"][loc_code]
                loc_items = [(c, it) for c, it in items.items() if it["location"] == loc_code]
                la = sum(it.get("qty_available", 0) for _, it in loc_items)
                lb = sum(it.get("qty_borrowed", 0) for _, it in loc_items)
                lr = sum(it.get("qty_repair", 0) for _, it in loc_items)
                f.write(f"\n  ▶ {loc_code} ({loc['description']})  {len(loc_items)}个品种  在库{la}/借出{lb}/维修{lr}\n")
                for code, it in loc_items:
                    sts = short_status_text(it)
                    f.write(f"      {code} | {it['name'][:16]} | {it['shelf']} | {sts}\n")

            if by_person_outstanding:
                f.write("\n【五、借用人未还责任汇总】\n")
                f.write(f"  共 {len(by_person_outstanding)} 人/单位  {sum(v['qty'] for v in by_person_outstanding.values())} 件未归还\n")
                f.write("  " + "-" * 56 + "\n")
                for person in sorted(by_person_outstanding.keys()):
                    info = by_person_outstanding[person]
                    f.write(f"\n  👤 {person}  —  未归还{info['qty']}件, 涉及{len(info['records'])}笔借出\n")
                    f.write(f"    {'单号':<12}{'编号':<7}{'玩具名称':<16}{'借':>3}{'未还':>4}  {'到期日':<12}{'用途/经手':<16}{'联系方式':<12}\n")
                    for rid, code, name, qty, ret, out, due, purpose, handler, contact in info["records"]:
                        ph = f"{purpose or ''}/{handler or ''}"
                        f.write(f"    {rid:<12}{code:<7}{name[:16]:<16}{qty:>3}{out:>4}  {due:<12}{ph[:16]:<16}{contact[:12]:<12}\n")
                f.write("\n  建议: 对逾期未还或长期未归还的借用人及时沟通催还\n")

            # 【六、借用人催还清单(按逾期/未还数排序)】
            dunning_map = defaultdict(lambda: {
                "qty": 0, "overdue_qty": 0, "recs": 0, "overdue_recs": 0,
                "contact": "", "handler": "", "latest_time": "", "records": []
            })
            today_dd = datetime.now().date()
            for rec in borrow_list:
                if rec["status"] in ("closed", "rejected") or rec.get("approval_status") == "rejected": continue
                out = rec["qty"] - rec["qty_returned"]
                if out <= 0: continue
                od = 0
                if rec["due_date"]:
                    try:
                        dd = datetime.strptime(rec["due_date"], "%Y-%m-%d").date()
                        if today_dd > dd: od = (today_dd - dd).days
                    except ValueError: pass
                last_h = rec.get("handler", "")
                last_t = rec.get("created_at", "")
                for h in rec.get("history", []):
                    if h.get("operator"):
                        last_h = h["operator"]
                        last_t = h.get("time", last_t)
                p = rec["borrower"]
                di = dunning_map[p]
                di["records"].append([rec["id"], rec["code"], rec["toy_name"],
                                     rec["qty"], out, rec["due_date"], od,
                                     rec.get("purpose", ""), rec.get("approval_status", "approved")])
                di["qty"] += out; di["recs"] += 1
                if od > 0: di["overdue_qty"] += out; di["overdue_recs"] += 1
                if rec.get("contact") and not di["contact"]: di["contact"] = rec["contact"]
                if not di["latest_time"] or last_t > di["latest_time"]:
                    di["latest_time"] = last_t
                    di["handler"] = last_h or rec.get("handler", "")
            if dunning_map:
                f.write("\n【六、借用人催还清单】\n")
                f.write("  (按逾期件数→未还件数排序, 可直接用于联系班级/家长)\n")
                f.write(f"  共 {len(dunning_map)} 位借用人  "
                        f"{sum(v['qty'] for v in dunning_map.values())} 件未还  "
                        f"{sum(v['overdue_qty'] for v in dunning_map.values())} 件逾期\n")
                f.write("  " + "-" * 92 + "\n")
                f.write(f"  {'借用人':<18}{'未结笔/件':>10}{'逾期笔/件':>10}  "
                        f"{'最近经手人':<12}{'联系方式':<14}{'备注':<20}\n")
                f.write("  " + "-" * 92 + "\n")
                for p in sorted(dunning_map.keys(),
                              key=lambda x: (-dunning_map[x]["overdue_qty"], -dunning_map[x]["qty"])):
                    di = dunning_map[p]
                    note = []
                    if di["overdue_qty"] > 0: note.append("紧急催还")
                    if not di["contact"]: note.append("联系方式缺失")
                    f.write(f"  {p[:18]:<18}"
                            f"{di['recs']:>4}/{di['qty']:<5}"
                            f"{di['overdue_recs']:>4}/{di['overdue_qty']:<5}  "
                            f"{(di['handler'] or '-')[:12]:<12}"
                            f"{(di['contact'] or '-')[:14]:<14}"
                            f"{'; '.join(note)[:20]:<20}\n")
                # 逾期优先明细
                urgent = [(p, di) for p, di in dunning_map.items() if di["overdue_qty"] > 0]
                if urgent:
                    f.write("\n  🔥 紧急催还明细 (按逾期天数排序):\n")
                    f.write(f"    {'借用人':<16}{'单号':<10}{'编号':<6}{'玩具名称':<16}"
                            f"{'未还':>4}  {'到期日':<12}{'逾期':>6}  {'用途/审批':<18}\n")
                    f.write("    " + "-" * 100 + "\n")
                    all_urgent_rows = []
                    for p, di in urgent:
                        for rid, code, name, qty, out, due, od, pur, appr in di["records"]:
                            if od > 0:
                                all_urgent_rows.append((od, p, rid, code, name, out, due, pur, appr))
                    all_urgent_rows.sort(key=lambda x: -x[0])
                    for od, p, rid, code, name, out, due, pur, appr in all_urgent_rows:
                        pa = f"{pur or ''}/{appr}"
                        f.write(f"    {p[:16]:<16}{rid:<10}{code:<6}{name[:16]:<16}"
                                f"{out:>4}  {(due or '-'):<12}{od:>5}天  {pa[:18]:<18}\n")

            f.write("\n" + "=" * 64 + "\n")
            f.write("              报告结束  —  请负责人审阅\n")
            f.write("=" * 64 + "\n")

        print(f"📄 TXT盘点报告 -> {txt_file}")

    log_action("EXPORT", f"导出盘点报告, 格式={fmt}, 前缀={output}")
    print(f"\n✅ 导出完成！请将报告文件发送给负责人审阅。")


# ==================== 主入口 ====================

def build_parser():
    parser = argparse.ArgumentParser(
        prog="toy-inventory",
        description="🏫 玩具仓库盘点命令行工具 V2 - 幼儿园/玩具租赁仓专用 (细分数量口径+借出台账+盘点会话)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
命令速查:
  init     初始化仓库库位结构
  import   导入玩具台账 (CSV/JSON)
  scan     扫码登记: inbound入库/borrow借出/return归还/renew续借/scrap报废/followup催跟进
  move     库位调拨 (单件/整库)
  check    盘点检查 / 盘点会话管理
  report   统计报告: diff/age/category/borrow/logs
  label    打印标签 / 安全库存 / 操作人
  export   导出盘点表 (CSV+借出台账/JSON/TXT)

盘点会话:
  check count-start   开启盘点会话(保存账面快照)
  check count-scan    录入实盘数量(支持交互或命令行)
  check count-review  复核环节: 补扫差异/--only-diff列差异清单
  check count-diff    查看账面vs实盘差异 (--id 回看历史会话)
  check count-export  按会话导出盘点差异表(CSV/TXT, 初盘/复核/最终)
  check count-trend   库位盘点趋势: 最近N次盘点差异变化/反复出问题排行
  check count-close   结束会话 [--apply --confirm 调账(需负责人二次确认)]
  check count-list    历史会话列表 (--id 回看会话详情+差异+调账)
  check count-abort   放弃当前会话

责任催还:
  check --dunning     打印借用人催还清单(支持 --overdue-only / --missing-contact / --borrower 筛选)
  scan followup       登记催跟进结果(已联系/承诺归还/暂缓/已处理)
        """
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="可用命令")

    # init
    p_init = subparsers.add_parser("init", help="初始化仓库库位")
    p_init.add_argument("--rows", type=int)
    p_init.add_argument("--cols", type=int)
    p_init.add_argument("--shelves", type=int)
    p_init.add_argument("--capacity", type=int)
    p_init.add_argument("--overdue-days", type=int)
    p_init.add_argument("--force", action="store_true")
    p_init.add_argument("--yes", action="store_true")
    p_init.set_defaults(func=cmd_init)

    # import
    p_imp = subparsers.add_parser("import", help="导入玩具台账")
    p_imp.add_argument("file")
    p_imp.add_argument("--default-location")
    p_imp.add_argument("--update", action="store_true")
    p_imp.set_defaults(func=cmd_import)

    # scan
    p_scan = subparsers.add_parser("scan", help="扫码操作")
    p_scan.add_argument("operation", choices=["inbound", "borrow", "return", "renew", "scrap", "followup"])
    p_scan.add_argument("--codes", nargs="+")
    p_scan.add_argument("--qty", type=int, default=1)
    p_scan.add_argument("--operator")
    p_scan.add_argument("--location")
    p_scan.add_argument("--shelf")
    p_scan.add_argument("--name")
    p_scan.add_argument("--category")
    p_scan.add_argument("--brand")
    p_scan.add_argument("--age")
    p_scan.add_argument("--price", type=float)
    p_scan.add_argument("-b", "--borrower")
    p_scan.add_argument("--due")
    p_scan.add_argument("--renew-days", type=int)
    p_scan.add_argument("--condition", choices=["完好", "轻微破损", "待维修", "破损"])
    p_scan.add_argument("--reason")
    p_scan.add_argument("--from-repair", action="store_true")
    p_scan.add_argument("--purpose")
    p_scan.add_argument("--handler")
    p_scan.add_argument("--contact")
    p_scan.add_argument("--approval", choices=["pending", "approved", "rejected"])
    p_scan.add_argument("--approve", action="store_true")
    p_scan.add_argument("--reject", action="store_true")
    p_scan.add_argument("--remark")
    # followup 催跟进专用参数
    p_scan.add_argument("--br-id", help="指定借出单号(用于followup精确登记)")
    p_scan.add_argument("--followup-status", dest="followup_status",
                        choices=["已联系", "承诺归还", "暂缓", "已处理", "无需催还"],
                        help="催跟进状态: 已联系/承诺归还/暂缓/已处理/无需催还")
    p_scan.add_argument("--promised-date", help="承诺归还日期(YYYY-MM-DD)")
    p_scan.add_argument("--followup-note", dest="followup_note", help="催跟进备注(沟通内容等)")
    p_scan.set_defaults(func=cmd_scan)

    # move
    p_move = subparsers.add_parser("move", help="库位调拨")
    p_move.add_argument("--codes", nargs="+")
    p_move.add_argument("--all-from")
    p_move.add_argument("--to", required=True)
    p_move.add_argument("--shelf")
    p_move.add_argument("--operator")
    p_move.set_defaults(func=cmd_move)

    # check
    p_check = subparsers.add_parser("check", help="盘点检查/盘点会话")
    p_check.add_argument("--location")
    p_check.add_argument("--all", action="store_true")
    p_check.add_argument("--borrowed", action="store_true")
    p_check.add_argument("--borrower")
    p_check.add_argument("--overdue", action="store_true")
    p_check.add_argument("--dunning", action="store_true", help="打印借用人催还清单(未还/逾期/联系方式/经手人)")
    # dunning 筛选参数
    p_check.add_argument("--overdue-only", action="store_true", help="催还清单仅显示逾期者")
    p_check.add_argument("--missing-contact", action="store_true", help="催还清单仅显示联系方式缺失者")
    p_check.add_argument("--dunning-limit", type=int, dest="dunning_limit", help="催还清单显示前N人(按逾期/未还排序)")
    p_check.add_argument("--yes", action="store_true")

    check_sub = p_check.add_subparsers(dest="count_cmd")

    cs_start = check_sub.add_parser("count-start", help="开启盘点会话")
    cs_start.add_argument("--location")
    cs_start.add_argument("--note")
    cs_start.add_argument("--operator")
    cs_start.add_argument("--force", action="store_true")
    cs_start.set_defaults(count_cmd="start", func=cmd_check)

    cs_scan = check_sub.add_parser("count-scan", help="录入实盘数量")
    cs_scan.add_argument("entries", nargs="*")
    cs_scan.add_argument("--interactive", action="store_true")
    cs_scan.set_defaults(count_cmd="scan", func=cmd_check)

    cs_diff = check_sub.add_parser("count-diff", help="查看差异 (--id指定历史会话)")
    cs_diff.add_argument("--id", help="指定历史会话ID回看差异")
    cs_diff.add_argument("--limit", type=int)
    cs_diff.set_defaults(count_cmd="diff", func=cmd_check)

    cs_review = check_sub.add_parser("count-review", help="复核环节：标记待复核或补扫差异")
    cs_review.add_argument("--reviewer", help="复核人(默认取默认操作人)")
    cs_review.add_argument("--only-diff", action="store_true", help="只显示当前有差异的编号提示补扫")
    cs_review.add_argument("entries", nargs="*")
    cs_review.add_argument("--interactive", action="store_true")
    cs_review.set_defaults(count_cmd="review", func=cmd_check)

    cs_export = check_sub.add_parser("count-export", help="按会话导出盘点差异表(CSV/TXT, 初盘/复核/最终)")
    cs_export.add_argument("--id", help="指定会话ID(默认取当前活跃会话)")
    cs_export.add_argument("--format", choices=["csv", "txt", "all"], default="all", help="导出格式")
    cs_export.add_argument("--output", help="输出路径(不含扩展名, 默认 count_{id})")
    cs_export.set_defaults(count_cmd="export", func=cmd_check)

    cs_trend = check_sub.add_parser("count-trend", help="库位盘点趋势: 最近N次差异变化/问题玩具排行")
    cs_trend.add_argument("--location", required=True, help="指定库位(如A01-01)")
    cs_trend.add_argument("--limit", type=int, default=5, help="回看最近N次盘点(默认5)")
    cs_trend.add_argument("--top", type=int, default=10, help="显示问题玩具TOP N(默认10)")
    cs_trend.add_argument("--export", help="导出趋势CSV到指定路径(可选)")
    cs_trend.set_defaults(count_cmd="trend", func=cmd_check)

    cs_close = check_sub.add_parser("count-close", help="结束会话(可选调账: --apply --confirm)")
    cs_close.add_argument("--apply", action="store_true", help="预览调账差异, 配合--confirm才真正写入")
    cs_close.add_argument("--confirm", action="store_true", help="负责人二次确认后才真正调账(必须与--apply同时使用)")
    cs_close.add_argument("--operator")
    cs_close.set_defaults(count_cmd="close", func=cmd_check)

    cs_list = check_sub.add_parser("count-list", help="历史会话 (--id回看详情+差异+调账)")
    cs_list.add_argument("--id", help="指定会话ID回看(含差异和调账明细)")
    cs_list.add_argument("--limit", type=int)
    cs_list.set_defaults(count_cmd="list", func=cmd_check)

    cs_abort = check_sub.add_parser("count-abort", help="放弃当前会话")
    cs_abort.add_argument("--yes", action="store_true")
    cs_abort.set_defaults(count_cmd="abort", func=cmd_check)

    p_check.set_defaults(func=cmd_check)

    # report
    p_rep = subparsers.add_parser("report", help="统计报告")
    p_rep.add_argument("type", choices=["diff", "age", "category", "borrow", "logs"])
    p_rep.add_argument("--file")
    p_rep.add_argument("--detail", type=int)
    p_rep.add_argument("--limit", type=int)
    p_rep.add_argument("--status", choices=["active", "partial", "overdue", "closed", "all"])
    p_rep.add_argument("--borrower")
    p_rep.set_defaults(func=cmd_report)

    # label
    p_lab = subparsers.add_parser("label", help="标签与设置")
    p_lab.add_argument("--codes", nargs="+")
    p_lab.add_argument("--all", action="store_true")
    p_lab.add_argument("--cols", type=int, default=2)
    p_lab.add_argument("--output")
    p_lab.add_argument("--safety", action="store_true")
    p_lab.add_argument("--category")
    p_lab.add_argument("--threshold", type=int)
    p_lab.add_argument("--list", action="store_true")
    p_lab.add_argument("--operator")
    p_lab.add_argument("--overdue", type=int)
    p_lab.set_defaults(func=cmd_label)

    # export
    p_exp = subparsers.add_parser("export", help="导出盘点表")
    p_exp.add_argument("--format", choices=["csv", "json", "txt", "all"], default="all")
    p_exp.add_argument("--output")
    p_exp.set_defaults(func=cmd_export)

    return parser


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n操作已取消")
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()