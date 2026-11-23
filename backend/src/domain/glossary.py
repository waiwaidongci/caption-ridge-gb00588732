"""预算内挑选术语组合，沿用已完成估算方案重算全批待审工时。"""

import re
from math import fsum

_POSITION_MAX = 10000
_POSITIONS_PER_TERM = 2000
_NODE_LIMIT = 5_000_000


def parse_coverage(text):
    """Parse lines of "工单号: 位置 位置 …" into a sorted, deduplicated position list."""
    if not isinstance(text, str):
        raise ValueError("可免核的工单位置格式不正确")
    positions = set()
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        job_id, separator, rest = line.replace("：", ":").partition(":")
        job_id = job_id.strip()
        if not separator or not job_id or len(job_id) > 64 or any(char.isspace() for char in job_id):
            raise ValueError(f"覆盖范围第 {lineno} 行请按「工单号: 位置序号」填写")
        tokens = [token for token in re.split(r"[\s,，、]+", rest) if token]
        if not tokens:
            raise ValueError(f"覆盖范围第 {lineno} 行至少填写一个位置序号")
        for token in tokens:
            if not token.isascii() or not token.isdigit() or not 1 <= int(token) <= _POSITION_MAX:
                raise ValueError(f"覆盖范围第 {lineno} 行的位置「{token}」应为 1–{_POSITION_MAX} 的整数")
            positions.add((job_id, int(token)))
    if not positions:
        raise ValueError("请至少登记一处可免核的工单位置")
    if len(positions) > _POSITIONS_PER_TERM:
        raise ValueError(f"单条术语最多登记 {_POSITIONS_PER_TERM} 处位置")
    return [{"job_id": job_id, "position": position} for job_id, position in sorted(positions)]


def serialize_coverage(coverage):
    """Render stored positions back to the canonical textarea lines."""
    grouped = {}
    for spot in coverage:
        grouped.setdefault(spot["job_id"], set()).add(spot["position"])
    return "\n".join(
        f"{job_id}: {' '.join(str(position) for position in sorted(spots))}"
        for job_id, spots in sorted(grouped.items()))


def _better(candidate, best):
    """Maximize saved minutes; break ties by lower cost, fewer terms, then smaller ids."""
    saved, cost, ids = candidate
    if abs(saved - best[0]) > 1e-6:
        return saved > best[0]
    if abs(cost - best[1]) > 1e-6:
        return cost < best[1]
    if len(ids) != len(best[2]):
        return len(ids) < len(best[2])
    return ids < best[2]


def _optimize(items, predictions, estimates, budget, slope):
    """Branch and bound over term subsets; coverage unions are per-order bitmasks."""
    size = len(predictions)

    def saved_in(order, bits):
        return estimates[order] - round(max(0.0, predictions[order] - slope * bits.bit_count()), 2)

    ordered = sorted(items, key=lambda item: (
        -fsum(saved_in(o, item["bits"][o]) for o in range(size)) / item["cost"], item["id"]))
    count = len(ordered)
    suffix = [[0] * size for _ in range(count + 1)]
    for index in range(count - 1, -1, -1):
        suffix[index] = [suffix[index + 1][o] | ordered[index]["bits"][o] for o in range(size)]

    best = (0.0, 0.0, ())
    nodes = 0

    def visit(index, cost, saved, unions, chosen):
        nonlocal best, nodes
        nodes += 1
        if nodes > _NODE_LIMIT:
            raise ValueError("候选术语过多，请分批整理后重试")
        if cost > budget + 1e-6:
            return
        ids = tuple(sorted(chosen))
        if _better((saved, cost, ids), best):
            best = (saved, cost, ids)
        if index == count:
            return
        bound = saved
        for o in range(size):
            available = suffix[index][o] & ~unions[o]
            if available:
                headroom = estimates[o] - saved_in(o, unions[o])
                bound += min(headroom, slope * available.bit_count() + 0.01)
        if bound < best[0] - 1e-6:
            return
        item = ordered[index]
        merged = [unions[o] | item["bits"][o] for o in range(size)]
        gain = fsum(saved_in(o, merged[o]) - saved_in(o, unions[o]) for o in range(size))
        visit(index + 1, cost + item["cost"], saved + gain, merged, chosen + [item["id"]])
        visit(index + 1, cost, saved, unions, chosen)

    visit(0, 0.0, 0.0, [0] * size, [])
    return best


def _table(title, columns, rows):
    return {"title": title, "columns": [{"key": key, "label": label} for key, label in columns], "rows": rows}


def _empty_result(headline, note, term_count):
    return {
        "state": "warning",
        "headline": headline,
        "note": note,
        "metrics": [{"label": "候选术语（条）", "value": term_count, "detail": "已录入术语库的术语"}],
        "tables": [],
        "sections": [],
    }


def plan_glossary(estimation, terms, budget, source):
    """Pick the budget-feasible term set saving the most pending-review minutes."""
    model = estimation.model
    pending = estimation.pending
    if not pending:
        return _empty_result("该估算方案的数据批次没有待审工单，整理术语不影响排班。",
                             f"{source}的队列中没有待审工单。", len(terms))
    slope = None
    if "term_count" in model.features:
        index = model.features.index("term_count")
        scale = model.scales[index]
        slope = model.weights[index] / scale if scale else 0.0
    if slope is None:
        return _empty_result("所选估算方案未使用待核术语数特征，整理术语不改变该方案的估算工时。",
                             f"{source}入选的特征组不含待核术语数，请改用含术语特征的估算方案。", len(terms))
    if not terms:
        return _empty_result("尚未录入术语，请先在术语库登记整理成本与可免核位置。",
                             f"{source}已就绪，术语库为空。", 0)
    if len(terms) > 200:
        raise ValueError("候选术语最多 200 条，请分批整理后重试")

    predictions = [model.predict(row) for row in pending]
    estimates = [round(max(0.0, value), 2) for value in predictions]
    index_of = {row["job_id"]: position for position, row in enumerate(pending)}
    unmatched = []
    items = []
    for term in sorted(terms, key=lambda item: item["id"]):
        bits = [0] * len(pending)
        touched = set()
        matched = 0
        for spot in term["coverage"]:
            order = index_of.get(spot["job_id"])
            if order is None or not 1 <= spot["position"] <= pending[order]["term_count"]:
                unmatched.append({"term": term["term"], "job_id": spot["job_id"], "position": spot["position"]})
                continue
            bit = 1 << (spot["position"] - 1)
            if not bits[order] & bit:
                bits[order] |= bit
                matched += 1
                touched.add(spot["job_id"])
        items.append({"id": term["id"], "term": term["term"], "cost": float(term["cost"]),
                      "matched": matched, "orders": len(touched), "bits": tuple(bits)})

    saved, cost, ids = _optimize(items, predictions, estimates, budget, slope) if slope > 0 else (0.0, 0.0, ())
    chosen = [item for item in items if item["id"] in set(ids)]
    unions = [0] * len(pending)
    for item in chosen:
        for order in range(len(pending)):
            unions[order] |= item["bits"][order]

    detail_rows = []
    for order, row in enumerate(pending):
        count = unions[order].bit_count()
        if not count:
            continue
        recomputed = round(max(0.0, predictions[order] - slope * count), 2)
        detail_rows.append({"job_id": row["job_id"], "job_date": row["job_date"], "title": row["title"],
                            "exempted": count, "estimated_minutes": estimates[order],
                            "recomputed_minutes": recomputed,
                            "saved_minutes": round(estimates[order] - recomputed, 2)})
    detail_rows.sort(key=lambda row: (-row["saved_minutes"], row["job_id"]))
    total_saved = round(fsum(row["saved_minutes"] for row in detail_rows), 2)
    total_cost = round(fsum(item["cost"] for item in chosen), 2)
    total_estimate = round(fsum(estimates), 2)
    exempted_total = sum(row["exempted"] for row in detail_rows)

    if chosen:
        headline = f"预算 {budget:g} 分钟内选定 {len(chosen)} 条术语，全批待审工时预计节省 {total_saved:g} 分钟。"
    else:
        headline = f"预算 {budget:g} 分钟内没有能节省待审工时的术语组合。"
    note = (f"沿用{source}的拟合模型重算：每免核一处待核术语约节省 {slope:.4f} 分钟，估算工时最低为零；"
            "同一位置被多条术语覆盖时只扣一次。")
    metrics = [
        {"label": "预计节省（分钟）", "value": total_saved, "detail": "全批待审工单重算后减少的估算工时合计"},
        {"label": "重算后待审工时（分钟）", "value": round(total_estimate - total_saved, 2),
         "detail": f"原估算合计 {total_estimate:g} 分钟"},
        {"label": "整理成本合计（分钟）", "value": total_cost, "detail": f"整理预算 {budget:g} 分钟"},
        {"label": "选中术语（条）", "value": len(chosen), "detail": f"候选术语 {len(items)} 条"},
        {"label": "免核位置（处）", "value": exempted_total, "detail": "同一位置被多条术语覆盖时只计一次"},
        {"label": "术语系数（分钟/处）", "value": round(slope, 4), "detail": "每免核一处待核术语减少的估算工时"},
    ]
    tables = []
    if chosen:
        tables.append(_table("所选术语与整理成本", (
            ("term", "术语"), ("cost", "整理成本（分钟）"), ("matched", "免核位置（处）"), ("orders", "覆盖工单（个）")),
            [{"term": item["term"], "cost": item["cost"], "matched": item["matched"], "orders": item["orders"]}
             for item in chosen]))
        tables.append(_table("逐工单节省明细", (
            ("job_id", "工单号"), ("job_date", "工单日期"), ("title", "讲座标题"),
            ("exempted", "免核位置（处）"), ("estimated_minutes", "原估算（分钟）"),
            ("recomputed_minutes", "重算估算（分钟）"), ("saved_minutes", "节省（分钟）")), detail_rows))
    sections = []
    if unmatched:
        examples = "、".join(f"{spot['job_id']}#{spot['position']}" for spot in unmatched[:20])
        sections.append({"title": "未匹配位置", "text": (
            f"{len(unmatched)} 处登记位置未匹配到本批待审工单（{examples}"
            f"{'等' if len(unmatched) > 20 else ''}），未计入节省。")})
    sections.append({"title": "重算口径", "text": (
        f"沿用{source}的拟合模型：待核术语每免核一处，该工单估算工时减少 {round(slope, 4)} 分钟，最低为零。"
        "同一位置被多条术语覆盖时只扣一次。在预算内精确比较所有候选组合，"
        "节省相同则依次取成本更低、术语更少、编号更小的组合。")})
    return {"state": "ok" if chosen else "warning", "headline": headline, "note": note,
            "metrics": metrics, "tables": tables, "sections": sections}
