from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib import error, request
import uuid

import pymysql
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field


# ================== MySQL 配置 ==================
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "rule_user",  # TODO: 改成你的 MySQL 用户名
    "password": "Strong_Pwd_123!",  # TODO: 改成你的 MySQL 密码
    "database": "valve_rule_db",  # TODO: 改成你的数据库名
    "charset": "utf8mb4",
}


def get_db_connection():
    """
    获取 MySQL 连接，返回 DictCursor。
    """
    return pymysql.connect(
        cursorclass=pymysql.cursors.DictCursor,
        **DB_CONFIG,
    )


# ================== 日志（更全面：JSON Lines + 按天轮转） ==================
LOG_DIR = os.getenv("VALVE_RULE_LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

DEBUG_TRACE_DEFAULT = os.getenv("DEBUG_TRACE", "0").strip() in (
    "1",
    "true",
    "True",
    "YES",
    "yes",
)

logger = logging.getLogger("valve_rule")
logger.setLevel(logging.INFO)

# 避免重复添加 handler（uvicorn reload 常见）
if not any(isinstance(h, TimedRotatingFileHandler) for h in logger.handlers):
    file_handler = TimedRotatingFileHandler(
        filename=os.path.join(LOG_DIR, "app.jsonl"),
        when="D",
        interval=1,
        backupCount=int(os.getenv("VALVE_RULE_LOG_RETENTION_DAYS", "30")),
        encoding="utf-8",
        utc=False,
    )
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(file_handler)

# 如你希望控制台也输出（容器/服务日志采集更方便），可打开：
if os.getenv("VALVE_RULE_LOG_TO_CONSOLE", "1").strip() in (
    "1",
    "true",
    "True",
    "YES",
    "yes",
):
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console_handler)


def _json_log(payload: Dict[str, Any]) -> None:
    """
    统一 JSON 结构化日志输出。默认把 date/datetime 等转为字符串。
    """
    try:
        logger.info(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        # 日志失败不影响主流程
        pass


def _truncate(v: Any, max_len: int = 200) -> Any:
    """
    防止日志 params 过长。字符串做截断，其他类型原样返回。
    """
    if isinstance(v, str) and len(v) > max_len:
        return v[:max_len] + "…"
    return v


# ================== Pydantic 模型 ==================
class RuleInput(BaseModel):
    """
    单条规则输入。注意：
    - id 用于调用方标识这一行，服务只透传，不参与匹配。
    - OrderApprovedDate / ReplyDeliveryDate / RequestedDeliveryDate 为日期字段。
    """

    model_config = ConfigDict(
        validate_by_name=True,
        populate_by_name=True,
    )

    # —— 调用方行ID，仅做透传 ——
    id: Optional[str] = Field(default=None, description="调用方行ID，仅透传返回")

    # ------- 规则字段 -------
    fa_men_da_lei: Optional[str] = None  # 阀门大类
    fa_men_lei_bie: Optional[str] = None  # 阀门类别
    chan_pin_ming_cheng: Optional[str] = None  # 产品名称
    gong_cheng_tong_jing: Optional[str] = None  # 公称通径
    gong_cheng_ya_li: Optional[str] = None  # 公称压力

    fa_ti_cai_zhi: Optional[str] = None  # 阀体材质
    nei_jian_cai_zhi: Optional[str] = None  # 内件材质
    mi_feng_mian_xing_shi: Optional[str] = None  # 密封面形式
    fa_lan_lian_jie: Optional[str] = None  # 法兰连接方式
    shang_gai_xing_shi: Optional[str] = None  # 上盖形式
    liu_liang_te_xing: Optional[str] = None  # 流量特性

    zhi_xing_ji_gou: Optional[str] = None  # 执行机构
    fu_jian_pei_zhi: Optional[str] = None  # 附件配置

    wai_gou_fa_ti: Optional[str] = None  # 外购阀体
    wai_gou_biao_zhi: Optional[str] = None  # 外购标志
    te_pin: Optional[str] = None  # 特品
    xu_hao: Optional[str] = None  # 序号

    # ------- 日期输入（请求里字段名就叫这三个） -------
    order_approved_date: Optional[date] = Field(
        default=None, alias="OrderApprovedDate", description="订单审核日期"
    )
    reply_delivery_date: Optional[date] = Field(
        default=None, alias="ReplyDeliveryDate", description="回复交期"
    )
    requested_delivery_date: Optional[date] = Field(
        default=None, alias="RequestedDeliveryDate", description="要求交期"
    )


# ================== 规则分组（从粗到细） ==================
FIELD_GROUPS: List[List[str]] = [
    ["fa_men_da_lei", "fa_men_lei_bie", "chan_pin_ming_cheng"],  # 大类/类别/产品名
    ["gong_cheng_tong_jing", "gong_cheng_ya_li"],  # 通径/压力
    ["fa_ti_cai_zhi", "nei_jian_cai_zhi"],  # 材质
    ["te_pin", "wai_gou_biao_zhi", "wai_gou_fa_ti"],  # 特品/外购
    ["zhi_xing_ji_gou", "fu_jian_pei_zhi"],  # 执行机构/附件
    [
        "fa_lan_lian_jie",
        "shang_gai_xing_shi",
        "liu_liang_te_xing",
        "mi_feng_mian_xing_shi",
    ],  # 结构细节
]


# ================== 工具：清洗空字符串 ==================
def clean_value(v):
    """
    把 "" / "   " 当成 None，其它原样返回。
    """
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        return s
    return v


def error_reason_from_status(code: int) -> str:
    """
    给批量结果/日志一个更直观的原因标签（比只看 404/409 更直观）。
    """
    return {
        400: "NO_RULE_FIELDS",
        404: "NO_MATCH",
        409: "AMBIGUOUS",
    }.get(code, "HTTP_ERROR")


def _outputs_to_preview(
    outputs: Set[Tuple[Any, Any]], limit: int = 10
) -> List[Dict[str, Any]]:
    """
    outputs: {(sheng_chan_xian, gu_ding_zhou_qi), ...}
    -> [{"sheng_chan_xian": ..., "gu_ding_zhou_qi": ...}, ...]
    """
    preview: List[Dict[str, Any]] = []
    for line, cycle in list(outputs)[:limit]:
        preview.append({"sheng_chan_xian": line, "gu_ding_zhou_qi": cycle})
    return preview


# ================== 基准排产日 D_base 逻辑 ==================
def pick_base(
    d_std: Optional[date],
    d_reply: Optional[date],
    d_cust: Optional[date],
) -> Optional[date]:
    """
    基准排产日统一规则（不考虑产能）：

    D_base = (Date标准 ≥ Date回复) ? Date回复 : max(Date标准, Date客户)
    """
    if d_std is not None and d_reply is not None:
        if d_std >= d_reply:
            return d_reply

        if d_cust is None or d_std >= d_cust:
            return d_std
        return d_cust

    if d_std is None and d_reply is not None:
        return d_reply

    if d_std is not None and d_reply is None:
        if d_cust is None or d_std >= d_cust:
            return d_std
        return d_cust

    return d_cust


# ================== 单条推断核心（复用连接 + trace） ==================
def infer_one_with_conn(
    data: RuleInput,
    conn,
    *,
    trace: Optional[List[Dict[str, Any]]] = None,
    include_trace_in_exception: bool = False,
) -> Dict[str, Any]:
    """
    使用已有连接，对单条 RuleInput 做推断。

    关键增强：
    - 每一轮筛选都会写入 trace：本轮新增字段、累计字段、命中行数、候选输出数量等
    - 404（NO_MATCH）会明确是哪一轮开始 0 行
    - 409（AMBIGUOUS）会展示最终仍多候选的预览（可在 trace 中看到每轮变化）
    """
    trace = trace if trace is not None else []

    # 至少要有一个“规则字段”被填（空字符串不算）
    rule_has_value = False
    for group in FIELD_GROUPS:
        for field in group:
            if clean_value(getattr(data, field)) is not None:
                rule_has_value = True
                break
        if rule_has_value:
            break

    if not rule_has_value:
        detail: Dict[str, Any] = {
            "message": "至少需要提供一个规则字段（如阀门大类、产品名称、公称通径等）",
            "reason": error_reason_from_status(400),
        }
        if include_trace_in_exception:
            detail["trace"] = trace
        raise HTTPException(status_code=400, detail=detail)

    conditions: List[str] = []
    params: List[Any] = []
    used_fields: List[str] = []

    cursor = conn.cursor()
    executed_round = -1

    for group_idx, group in enumerate(FIELD_GROUPS):
        group_added = False
        added_fields: List[str] = []

        for field in group:
            raw_value = getattr(data, field)
            value = clean_value(raw_value)
            if value is None:
                continue

            conditions.append(f"{field} = %s")
            params.append(value)
            used_fields.append(field)
            added_fields.append(field)
            group_added = True

        if not group_added:
            continue

        executed_round = group_idx

        sql = "SELECT sheng_chan_xian, gu_ding_zhou_qi FROM valve_rule"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        cursor.execute(sql, params)
        rows = cursor.fetchall()

        outputs: Set[Tuple[Any, Any]] = set()
        if rows:
            outputs = {(r["sheng_chan_xian"], r["gu_ding_zhou_qi"]) for r in rows}

        trace.append(
            {
                "group_index": group_idx,
                "group_fields": list(group),
                "added_fields": added_fields,
                "used_fields": list(used_fields),
                "where": " AND ".join(conditions),
                "params": [_truncate(p) for p in params],
                "matched_rule_count": len(rows),
                "distinct_output_count": len(outputs),
                "outputs_preview": _outputs_to_preview(outputs, limit=10),
            }
        )

        # 这一轮直接变成 0 行：说明“加了这些筛选字段后，没有任何规则匹配”
        if not rows:
            detail = {
                "message": "当前组合条件未匹配到任何规则",
                "reason": error_reason_from_status(404),
                "used_fields": list(used_fields),
                "failed_group_index": group_idx,
                "failed_added_fields": added_fields,
            }
            if include_trace_in_exception:
                detail["trace"] = trace
            raise HTTPException(status_code=404, detail=detail)

        # 这一轮已经唯一：直接确定输出
        if len(outputs) == 1:
            line, cycle = next(iter(outputs))

            # 1) StandardDeliveryDate = OrderApprovedDate + gu_ding_zhou_qi
            standard_delivery_date: Optional[date] = None
            if data.order_approved_date is not None and cycle is not None:
                standard_delivery_date = data.order_approved_date + timedelta(
                    days=int(cycle)
                )

            # 2) ScheduleDate = D_base
            schedule_date: Optional[date] = pick_base(
                d_std=standard_delivery_date,
                d_reply=data.reply_delivery_date,
                d_cust=data.requested_delivery_date,
            )

            return {
                "status": "ok",
                "id": data.id,  # 透传调用方ID
                "sheng_chan_xian": line,
                "gu_ding_zhou_qi": int(cycle) if cycle is not None else cycle,
                "matched_rule_count": len(rows),
                "used_fields": used_fields,
                "StandardDeliveryDate": standard_delivery_date,
                "ScheduleDate": schedule_date,
            }

        # outputs>1：继续下一轮加更细字段

    # 走到这里：说明每次查都有 rows，但始终 outputs>1，最终仍无法唯一
    last_outputs_preview: List[Dict[str, Any]] = []
    last_distinct_output_count: Optional[int] = None
    if trace:
        last_outputs_preview = trace[-1].get("outputs_preview", [])
        last_distinct_output_count = trace[-1].get("distinct_output_count")

    detail = {
        "message": "根据当前所有输入字段仍无法唯一确定生产线和固定周期，请人工确认或细化规则",
        "reason": error_reason_from_status(409),
        "used_fields": list(used_fields),
        "last_group_index": executed_round,
        "last_distinct_output_count": last_distinct_output_count,
        "last_outputs_preview": last_outputs_preview,
    }
    if include_trace_in_exception:
        detail["trace"] = trace
    raise HTTPException(status_code=409, detail=detail)


# ================== 单条推断封装（写结构化日志） ==================
def do_infer(data: RuleInput, *, debug_trace: bool) -> Dict[str, Any]:
    request_id = uuid.uuid4().hex
    conn = get_db_connection()
    trace: List[Dict[str, Any]] = []
    started_at = datetime.now()

    try:
        result = infer_one_with_conn(
            data,
            conn,
            trace=trace,
            include_trace_in_exception=debug_trace,
        )

        _json_log(
            {
                "event": "infer_ok",
                "request_id": request_id,
                "id": data.id,
                "started_at": started_at,
                "ended_at": datetime.now(),
                "duration_ms": int(
                    (datetime.now() - started_at).total_seconds() * 1000
                ),
                "result": result,
                "trace": trace,  # 日志里永远保留全量 trace（最核心诊断信息）
            }
        )

        resp = {"request_id": request_id, **result}
        if debug_trace:
            resp["trace"] = trace
        return resp

    except HTTPException as ex:
        _json_log(
            {
                "event": "infer_fail",
                "request_id": request_id,
                "id": data.id,
                "started_at": started_at,
                "ended_at": datetime.now(),
                "duration_ms": int(
                    (datetime.now() - started_at).total_seconds() * 1000
                ),
                "error_code": ex.status_code,
                "reason": error_reason_from_status(ex.status_code),
                "detail": ex.detail,
                "trace": trace,  # 日志里永远保留全量 trace
            }
        )
        raise ex

    except Exception as e:
        _json_log(
            {
                "event": "infer_fail",
                "request_id": request_id,
                "id": data.id,
                "started_at": started_at,
                "ended_at": datetime.now(),
                "duration_ms": int(
                    (datetime.now() - started_at).total_seconds() * 1000
                ),
                "error_code": 500,
                "reason": "SERVER_ERROR",
                "error": str(e),
                "trace": trace,
            }
        )
        raise HTTPException(
            status_code=500, detail={"message": str(e), "reason": "SERVER_ERROR"}
        )
    finally:
        conn.close()


# ================== FastAPI 初始化 ==================
app = FastAPI(
    title="Valve Rule Service",
    description="根据输入字段推断 生产线 & 固定周期，并计算标准交期和排产日期（D_base）",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================== 单条 infer 接口 ==================
@app.post("/infer", summary="单条：推断生产线、固定周期、标准交期和排产日期")
def infer_line_and_cycle(
    data: RuleInput,
    debug_trace: bool = Query(
        default=DEBUG_TRACE_DEFAULT, description="是否在响应体中返回 trace（用于调试）"
    ),
):
    return do_infer(data, debug_trace=debug_trace)


# ================== 批量 batch_infer 接口（更全面日志） ==================
@app.post("/batch_infer", summary="批量：一次提交多条规则，逐条返回结果并生成日志")
def batch_infer(
    items: List[RuleInput],
    debug_trace: bool = Query(
        default=DEBUG_TRACE_DEFAULT, description="是否在响应体中返回 trace（用于调试）"
    ),
):
    """
    增强点：
    - batch_id / request_id 结构化日志（logs/app.jsonl），每日轮转
    - 每条 item 记录 trace（每轮筛选的命中行数/候选输出数量等）
    - 返回结果会明确区分 NO_MATCH（404）还是 AMBIGUOUS（409）
    - 仍保留一个 batch_*.json 汇总文件（便于离线查看）
    """
    batch_id = uuid.uuid4().hex
    conn = get_db_connection()
    started_at = datetime.now()

    results: List[Dict[str, Any]] = []
    stats = {
        "ok": 0,
        "NO_RULE_FIELDS": 0,
        "NO_MATCH": 0,
        "AMBIGUOUS": 0,
        "SERVER_ERROR": 0,
        "HTTP_ERROR": 0,
    }

    try:
        for idx, item in enumerate(items):
            request_id = uuid.uuid4().hex
            trace: List[Dict[str, Any]] = []
            item_started = datetime.now()

            try:
                r = infer_one_with_conn(
                    item,
                    conn,
                    trace=trace,
                    include_trace_in_exception=debug_trace,
                )

                stats["ok"] += 1

                _json_log(
                    {
                        "event": "batch_item_ok",
                        "batch_id": batch_id,
                        "request_id": request_id,
                        "index": idx,
                        "id": item.id,
                        "started_at": item_started,
                        "ended_at": datetime.now(),
                        "duration_ms": int(
                            (datetime.now() - item_started).total_seconds() * 1000
                        ),
                        "result": r,
                        "trace": trace,
                    }
                )

                item_resp: Dict[str, Any] = {
                    "index": idx,
                    "request_id": request_id,
                    "id": item.id,
                    "success": True,
                    "result": r,
                }
                if debug_trace:
                    item_resp["trace"] = trace
                results.append(item_resp)

            except HTTPException as ex:
                reason = error_reason_from_status(ex.status_code)
                stats[reason] = stats.get(reason, 0) + 1

                _json_log(
                    {
                        "event": "batch_item_fail",
                        "batch_id": batch_id,
                        "request_id": request_id,
                        "index": idx,
                        "id": item.id,
                        "started_at": item_started,
                        "ended_at": datetime.now(),
                        "duration_ms": int(
                            (datetime.now() - item_started).total_seconds() * 1000
                        ),
                        "error_code": ex.status_code,
                        "reason": reason,
                        "detail": ex.detail,
                        "trace": trace,
                    }
                )

                item_resp = {
                    "index": idx,
                    "request_id": request_id,
                    "id": item.id,
                    "success": False,
                    "error_code": ex.status_code,
                    "reason": reason,
                    "error": ex.detail,
                }
                if debug_trace and isinstance(ex.detail, dict) and "trace" not in ex.detail:
                    item_resp["trace"] = trace
                results.append(item_resp)

            except Exception as e:
                stats["SERVER_ERROR"] += 1

                _json_log(
                    {
                        "event": "batch_item_fail",
                        "batch_id": batch_id,
                        "request_id": request_id,
                        "index": idx,
                        "id": item.id,
                        "started_at": item_started,
                        "ended_at": datetime.now(),
                        "duration_ms": int(
                            (datetime.now() - item_started).total_seconds() * 1000
                        ),
                        "error_code": 500,
                        "reason": "SERVER_ERROR",
                        "error": str(e),
                        "trace": trace,
                    }
                )

                item_resp = {
                    "index": idx,
                    "request_id": request_id,
                    "id": item.id,
                    "success": False,
                    "error_code": 500,
                    "reason": "SERVER_ERROR",
                    "error": str(e),
                }
                if debug_trace:
                    item_resp["trace"] = trace
                results.append(item_resp)

        # ===== 写 batch 汇总日志（JSON 文件，便于离线查看） =====
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_path = os.path.join(LOG_DIR, f"batch_{timestamp}_{batch_id}.json")

        log_content = {
            "timestamp": timestamp,
            "batch_id": batch_id,
            "started_at": started_at,
            "ended_at": datetime.now(),
            "duration_ms": int((datetime.now() - started_at).total_seconds() * 1000),
            "total": len(items),
            "stats": stats,
            "results": results,
        }

        try:
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(log_content, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            _json_log(
                {
                    "event": "batch_log_write_fail",
                    "batch_id": batch_id,
                    "error": str(e),
                    "target": log_path,
                }
            )

        # batch 级别结构化日志（app.jsonl）
        _json_log(
            {
                "event": "batch_done",
                "batch_id": batch_id,
                "started_at": started_at,
                "ended_at": datetime.now(),
                "duration_ms": int((datetime.now() - started_at).total_seconds() * 1000),
                "total": len(items),
                "stats": stats,
                "log_file": log_path,
            }
        )

        return {
            "batch_id": batch_id,
            "total": len(items),
            "stats": stats,
            "results": results,
            "log_file": log_path,
        }
    finally:
        conn.close()


# ================== FMZD 同步（SeacherFMZD） ==================
FMZD_API_URL = os.getenv("FMZD_API_URL", "http://10.11.0.101:8003/gateway/SeacherFMZD")
FMZD_TIMEOUT_SECONDS = float(os.getenv("FMZD_TIMEOUT_SECONDS", "30"))

# 接口返回字段 -> valve_rule(拼音字段) 映射
# 约定：FENTRYID 作为“序号”，落到 xu_hao
FMZD_FIELD_MAP: Dict[str, str] = {
    "xu_hao": "FENTRYID",
    "chan_pin_ming_cheng": "F_ORA_NAME",
    "gong_cheng_tong_jing": "F_ORA_GCTJ",
    "gong_cheng_ya_li": "F_ORA_GCYL",
    "fa_ti_cai_zhi": "F_ORA_FTCZ",
    "nei_jian_cai_zhi": "F_ORA_NJCZ",
    "fa_lan_lian_jie": "F_ORA_FLLJFS",
    "shang_gai_xing_shi": "F_ORA_SGXS",
    "mi_feng_mian_xing_shi": "F_ORA_MFMXS",
    "liu_liang_te_xing": "F_ORA_LLTZ",
    "zhi_xing_ji_gou": "F_ORA_ZXJG",
    "wai_gou_fa_ti": "F_ORA_WGFT",
    "fa_men_da_lei": "F_ORA_FMDL",
    "fa_men_lei_bie": "F_ORA_FMLB",
    "sheng_chan_xian": "F_ORA_SCX",
    "gu_ding_zhou_qi": "F_ORA_GDZQ",
    "te_pin": "F_ORA_TP",
    "wai_gou_biao_zhi": "F_ORA_WGBZ",
    "fu_jian_pei_zhi": "F_ORA_FJPZ",
}

_valve_rule_columns_cache: Optional[Set[str]] = None


def _to_int(v: Any, default: int = 0) -> int:
    v2 = clean_value(v)
    if v2 is None:
        return default
    try:
        return int(float(str(v2).strip()))
    except Exception:
        return default


def fetch_fmzd(start_date: date, end_date: date) -> List[Dict[str, Any]]:
    """
    调用 FMZD 接口拉取数据（不依赖第三方库，使用 urllib）。
    说明：接口采用日期区间 [FSTARTDATE, FENDDATE)（你现在的用法是 start=当天, end=次日）。
    """
    payload = {
        "FSTARTDATE": start_date.strftime("%Y-%m-%d"),
        "FENDDATE": end_date.strftime("%Y-%m-%d"),
    }

    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            FMZD_API_URL,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=FMZD_TIMEOUT_SECONDS) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(resp_body)
    except error.HTTPError as e:
        # 读取错误响应体（尽量）
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        raise HTTPException(
            status_code=502,
            detail=f"FMZD接口HTTP错误: {getattr(e, 'code', '')} {err_body[:300]}",
        )
    except error.URLError as e:
        raise HTTPException(status_code=502, detail=f"FMZD接口网络错误: {str(e)[:300]}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"FMZD接口调用失败: {str(e)[:300]}")

    if not isinstance(data, list):
        raise HTTPException(status_code=502, detail=f"FMZD接口返回非list: {type(data)}")

    # 统一成 dict list
    return [x for x in data if isinstance(x, dict)]


def _get_valve_rule_columns(conn) -> Set[str]:
    """
    动态读取 valve_rule 字段集合（缓存一次），避免你表结构有增减导致插入失败。
    """
    global _valve_rule_columns_cache
    if _valve_rule_columns_cache is not None:
        return _valve_rule_columns_cache

    cols: Set[str] = set()
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT COLUMN_NAME
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'valve_rule'
            """
        )
        for r in cursor.fetchall() or []:
            name = r.get("COLUMN_NAME")
            if name:
                cols.add(str(name))
    _valve_rule_columns_cache = cols
    return cols


def map_fmzd_item(item: Dict[str, Any], *, available_cols: Set[str]) -> Dict[str, Any]:
    """
    把单条 FMZD 返回记录映射为可写入 valve_rule 的 row dict。
    - 空字符串/空格 -> None
    - gu_ding_zhou_qi 强制转 int（兜底 0）
    - 只保留当前表真实存在的列
    """
    row: Dict[str, Any] = {}

    for dst_col, src_key in FMZD_FIELD_MAP.items():
        if dst_col not in available_cols:
            continue

        if dst_col == "gu_ding_zhou_qi":
            row[dst_col] = _to_int(item.get(src_key), default=0)
            continue

        if dst_col == "xu_hao":
            v = item.get(src_key)
            row[dst_col] = "" if v is None else str(v).strip()
            continue

        row[dst_col] = clean_value(item.get(src_key))

    # 一些常见兜底（避免 NOT NULL 报错）
    if "sheng_chan_xian" in available_cols:
        row["sheng_chan_xian"] = row.get("sheng_chan_xian") or ""

    if "xu_hao" in available_cols and (row.get("xu_hao") is None or row.get("xu_hao") == ""):
        row["_skip_reason"] = "xu_hao为空"
    return row


def upsert_valve_rules(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    批量 upsert 到 MySQL valve_rule。
    关键点：
    - 推荐给 xu_hao 建唯一键：ALTER TABLE valve_rule ADD UNIQUE KEY uk_valve_rule_xu_hao (xu_hao);
    - 如果你表上没有任何唯一键，ON DUPLICATE KEY UPDATE 不会生效（可能产生重复）。
    """
    conn = get_db_connection()
    try:
        available_cols = _get_valve_rule_columns(conn)

        # 只写入“表里存在的列”
        write_cols = [c for c in FMZD_FIELD_MAP.keys() if c in available_cols]
        if not write_cols:
            raise HTTPException(
                status_code=500, detail="valve_rule表未包含任何可写入字段（字段名不匹配）"
            )

        # 确保固定周期/生产线（推断依赖）优先存在
        for must in ("sheng_chan_xian", "gu_ding_zhou_qi"):
            if must in available_cols and must not in write_cols:
                write_cols.append(must)

        # xu_hao 最好排在第一列（便于阅读，也更像主键）
        if "xu_hao" in write_cols:
            write_cols = ["xu_hao"] + [c for c in write_cols if c != "xu_hao"]

        placeholders = ",".join(["%s"] * len(write_cols))
        col_list = ",".join(write_cols)

        sql = f"INSERT INTO valve_rule ({col_list}) VALUES ({placeholders})"

        # 有唯一键时：ON DUPLICATE KEY UPDATE 才能实现幂等同步
        if "xu_hao" in write_cols:
            update_cols = [c for c in write_cols if c != "xu_hao"]
            if update_cols:
                update_list = ",".join([f"{c}=VALUES({c})" for c in update_cols])
                sql += " ON DUPLICATE KEY UPDATE " + update_list

        inserted = 0
        updated = 0
        unchanged_or_unknown = 0
        skipped = 0

        with conn.cursor() as cursor:
            for r in rows:
                if r.get("_skip_reason"):
                    skipped += 1
                    continue

                params = [r.get(c) for c in write_cols]
                cursor.execute(sql, params)

                # MySQL: insert=1; update=2; no-change=0(可能)
                rc = cursor.rowcount
                if rc == 1:
                    inserted += 1
                elif rc == 2:
                    updated += 1
                else:
                    unchanged_or_unknown += 1

        conn.commit()
        return {
            "inserted": inserted,
            "updated": updated,
            "unchanged_or_unknown": unchanged_or_unknown,
            "skipped": skipped,
            "processed": inserted + updated + unchanged_or_unknown,
            "write_cols": write_cols,
        }
    finally:
        conn.close()


class OrderInput(BaseModel):
    """订单输入（用于批量优化）"""

    order_id: str = Field(..., description="订单号")
    product_name: str = Field(..., description="产品名称")
    valve_type: str = Field(..., description="阀类")
    diameter: str = Field(..., description="通径")
    pressure: str = Field(..., description="压力")
    model: Optional[str] = Field(default=None, description="型号")
    quantity: int = Field(..., description="订单数量")
    schedule_date: date = Field(..., description="计划排产日期")
    line_id: str = Field(..., description="产线编号")
    cycle_base: Optional[int] = Field(default=None, description="周期基准")
    fixed_cycle: Optional[int] = Field(default=None, description="固定周期")


class ThresholdRecord(BaseModel):
    """产线阈值记录"""

    line_id: str
    schedule_date: date
    current_threshold: int
    current_quantity: int


class CapacityAssignment(BaseModel):
    """阈值约束排产结果"""

    order_id: str
    line_id: str
    original_date: date
    assigned_date: date
    shift_days: int
    reason: str


class RuleMatchTrace(BaseModel):
    """规则命中 trace"""

    order_id: str
    match_status: str
    matched_rule_id: Optional[str] = None
    similarity: Optional[float] = None
    fallback: bool = False
    events: List[Dict[str, Any]] = Field(default_factory=list)


class OptimizationJob(BaseModel):
    """批量优化任务"""

    job_id: str
    created_at: datetime
    status: str
    total: int
    optimized: int
    exceptions: int
    message: str


class OptimizationResult(BaseModel):
    """批量优化输出"""

    job: OptimizationJob
    orders: List[OrderInput]
    capacities: List[CapacityAssignment]
    traces: List[RuleMatchTrace]
    exceptions: List[Dict[str, Any]]


class SimpleEmbedding:
    """简化的 Embedding 推理（占位实现）"""

    def encode(self, text: str) -> List[float]:
        tokens = [ord(ch) % 97 for ch in text if ch.strip()]
        if not tokens:
            return [0.0]
        total = sum(tokens)
        length = len(tokens)
        return [total / length, max(tokens), min(tokens), float(length)]


class SimpleFaissIndex:
    """简化的 FAISS TopK 检索（占位实现）"""

    def __init__(self) -> None:
        self._vectors: Dict[str, List[float]] = {}

    def add(self, rule_id: str, vector: List[float]) -> None:
        self._vectors[rule_id] = vector

    def search(self, vector: List[float], top_k: int = 5) -> List[Tuple[str, float]]:
        results: List[Tuple[str, float]] = []
        for rule_id, stored in self._vectors.items():
            score = self._cosine_similarity(vector, stored)
            results.append((rule_id, score))
        results.sort(key=lambda item: item[1], reverse=True)
        return results[:top_k]

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        if not a or not b:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


class RuleIndexService:
    """规则向量检索服务（Embedding + FAISS）"""

    def __init__(self) -> None:
        self.embedding = SimpleEmbedding()
        self.index = SimpleFaissIndex()

    def build_query_text(self, order: OrderInput) -> str:
        return _select_query_text(order, mode="v1")

    def add_rule_vector(self, rule_id: str, rule_text: str) -> None:
        self.index.add(rule_id, self.embedding.encode(rule_text))

    def search(self, order: OrderInput, top_k: int = 5) -> List[Tuple[str, float]]:
        query_text = self.build_query_text(order)
        return self.index.search(self.embedding.encode(query_text), top_k=top_k)


class CapacityScheduler:
    """阈值约束排产日期计算"""

    def __init__(self) -> None:
        self._capacity: Dict[Tuple[str, date], ThresholdRecord] = {}

    def seed_capacity(self, record: ThresholdRecord) -> None:
        key = _make_capacity_key(record.line_id, record.schedule_date)
        self._capacity[key] = record

    def _get_capacity(self, line_id: str, target_date: date) -> ThresholdRecord:
        key = _make_capacity_key(line_id, target_date)
        if key not in self._capacity:
            self._capacity[key] = ThresholdRecord(
                line_id=_normalize_order_line(OrderInput(
                    order_id="__seed__",
                    product_name="",
                    valve_type="",
                    diameter="",
                    pressure="",
                    quantity=0,
                    schedule_date=target_date,
                    line_id=line_id,
                )),
                schedule_date=target_date,
                current_threshold=100,
                current_quantity=0,
            )
        return self._capacity[key]

    def assign(
        self, orders: List[OrderInput], window_days: int = 30
    ) -> List[CapacityAssignment]:
        results: List[CapacityAssignment] = []
        line_groups: Dict[str, List[OrderInput]] = {}
        for order in orders:
            normalized_line = _normalize_order_line(order)
            order.line_id = normalized_line
            order.quantity = _normalize_quantity(order.quantity)
            line_groups.setdefault(order.line_id, []).append(order)

        for line_id, line_orders in line_groups.items():
            line_orders.sort(key=lambda o: o.schedule_date)
            for order in line_orders:
                assigned_date = self._find_date(order, window_days)
                results.append(
                    _assign_capacity_result(order, assigned_date, order.schedule_date)
                )
        return results

    def _find_date(self, order: OrderInput, window_days: int) -> date:
        target = order.schedule_date
        for _ in range(window_days):
            record = self._get_capacity(order.line_id, target)
            if record.current_quantity + order.quantity <= record.current_threshold:
                record.current_quantity += order.quantity
                return target
            target = target + timedelta(days=1)
        return target


class OptimizationService:
    """智能体优化核心服务"""

    def __init__(self, rule_index: RuleIndexService, scheduler: CapacityScheduler) -> None:
        self.rule_index = rule_index
        self.scheduler = scheduler

    def match_rule(self, order: OrderInput, top_k: int = 5) -> RuleMatchTrace:
        events: List[Dict[str, Any]] = []
        query_v1 = _build_query_text_v1(order)
        query_v2 = _build_query_text_v2(order)
        query_v3 = _build_query_text_v3(order)
        query_v4 = _build_query_text_v4(order)
        events.append(_trace_event("query_v1", {"text": query_v1}))
        events.append(_trace_event("query_v2", {"text": query_v2}))
        events.append(_trace_event("query_v3", {"text": query_v3}))
        events.append(_trace_event("query_v4", {"text": query_v4}))

        candidates = self.rule_index.search(order, top_k=top_k)
        events.append(_trace_event("faiss_topk", {"candidates": candidates}))

        if not candidates:
            events.append(_trace_event("fallback", {"reason": "召回不足"}))
            return RuleMatchTrace(order_id=order.order_id, match_status="none", events=events)

        rule_id, similarity = candidates[0]
        status = "unique" if similarity > 0 else "multiple"
        return RuleMatchTrace(
            order_id=order.order_id,
            match_status=status,
            matched_rule_id=rule_id,
            similarity=similarity,
            events=events,
        )

    def optimize(
        self, orders: List[OrderInput], operator: str = "system"
    ) -> OptimizationResult:
        job = OptimizationJob(
            job_id=f"JOB-{uuid.uuid4().hex}",
            created_at=datetime.utcnow(),
            status="running",
            total=len(orders),
            optimized=0,
            exceptions=0,
            message="任务执行中",
        )

        traces: List[RuleMatchTrace] = []
        exceptions: List[Dict[str, Any]] = []
        for order in orders:
            trace = self.match_rule(order)
            traces.append(trace)
            if trace.match_status in {"unique"}:
                _apply_cycle_defaults(order)
                job.optimized += 1
            else:
                job.exceptions += 1
                exceptions.append(
                    _build_exception(
                        order.order_id, trace.match_status, "规则未唯一命中"
                    )
                )

        capacities = self.scheduler.assign(orders)
        job.status = "done"
        job.message = "任务已完成"
        return OptimizationResult(
            job=job,
            orders=orders,
            capacities=capacities,
            traces=traces,
            exceptions=exceptions,
        )


def _build_query_text_v1(order: OrderInput) -> str:
    # 方案一
    """构建检索文本（版本1）。"""
    parts = [
        order.product_name,
        order.valve_type,
        order.diameter,
        order.pressure,
        order.model or "",
    ]
    return " ".join([p for p in parts if p])


def _build_query_text_v2(order: OrderInput) -> str:
    # 方案二
    """构建检索文本（版本2，字段顺序调整）。"""
    parts = [
        order.valve_type,
        order.product_name,
        order.model or "",
        order.diameter,
        order.pressure,
    ]
    return " ".join([p for p in parts if p])


def _build_query_text_v3(order: OrderInput) -> str:
    # 方案三
    """构建检索文本（版本3，带分隔符）。"""
    parts = [
        f"产品:{order.product_name}",
        f"阀类:{order.valve_type}",
        f"通径:{order.diameter}",
        f"压力:{order.pressure}",
        f"型号:{order.model or ''}",
    ]
    return " | ".join([p for p in parts if p])


def _build_query_text_v4(order: OrderInput) -> str:
    # 方案四
    """构建检索文本（版本4，简化拼接）。"""
    return f"{order.product_name} {order.valve_type} {order.diameter} {order.pressure} {order.model or ''}".strip()


def _select_query_text(order: OrderInput, mode: str = "v1") -> str:
    # 查询文本路由
    """根据 mode 选择查询文本构建方式。"""
    if mode == "v2":
        return _build_query_text_v2(order)
    if mode == "v3":
        return _build_query_text_v3(order)
    if mode == "v4":
        return _build_query_text_v4(order)
    return _build_query_text_v1(order)


def _normalize_order_line(order: OrderInput) -> str:
    # 产线兜底
    """订单产线字段规范化（简单兜底）。"""
    value = (order.line_id or "").strip()
    if not value:
        return "UNKNOWN_LINE"
    return value


def _normalize_quantity(quantity: int) -> int:
    # 数量兜底
    """数量兜底处理。"""
    if quantity is None:
        return 0
    if quantity < 0:
        return 0
    return int(quantity)


def _make_capacity_key(line_id: str, schedule_date: date) -> Tuple[str, date]:
    # 容量键生成
    """构造产线容量 key。"""
    return (line_id, schedule_date)


def _to_capacity_reason(shift_days: int) -> str:
    # 生成原因说明
    """生成阈值排产原因。"""
    if shift_days <= 0:
        return "阈值满足"
    if shift_days == 1:
        return "超过阈值顺延1天"
    return f"超过阈值顺延{shift_days}天"


def _build_exception(order_id: str, match_status: str, message: str) -> Dict[str, Any]:
    # 统一异常格式
    """构造异常输出。"""
    return {
        "order_id": order_id,
        "reason": message,
        "match_status": match_status,
    }


def _trace_event(step: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    # Trace 事件封装
    """构建 trace event。"""
    return {
        "step": step,
        "payload": payload,
        "time": datetime.utcnow().isoformat(),
    }


def _apply_cycle_defaults(order: OrderInput) -> None:
    # 周期字段兜底
    """补齐周期字段（兜底）。"""
    if order.fixed_cycle is None:
        order.fixed_cycle = 0
    if order.cycle_base is None:
        order.cycle_base = 0


def _assign_capacity_result(
    order: OrderInput, assigned_date: date, original_date: date
) -> CapacityAssignment:
    # 结果封装
    """构造 CapacityAssignment。"""
    shift_days = (assigned_date - original_date).days
    return CapacityAssignment(
        order_id=order.order_id,
        line_id=order.line_id,
        original_date=original_date,
        assigned_date=assigned_date,
        shift_days=shift_days,
        reason=_to_capacity_reason(shift_days),
    )


rule_index_service = RuleIndexService()
capacity_scheduler = CapacityScheduler()
optimization_service = OptimizationService(rule_index_service, capacity_scheduler)
optimization_jobs: Dict[str, OptimizationResult] = {}


@app.post(
    "/sync_fmzd_today",
    summary="同步FMZD：按天增量拉取并写入 valve_rule（start=当天, end=次日）",
)
def sync_fmzd_today(
    target_date: Optional[str] = Query(
        default=None, description="要同步哪一天(YYYY-MM-DD)。不填则取服务器当天。"
    ),
    dry_run: bool = Query(default=False, description="只拉取不落库，返回前5条映射样例。"),
):
    # 只同步当天：start=当天，end=次日
    if target_date:
        try:
            start = datetime.strptime(target_date, "%Y-%m-%d").date()
        except Exception:
            raise HTTPException(status_code=400, detail="target_date格式错误，应为YYYY-MM-DD")
    else:
        start = datetime.now().date()

    end = start + timedelta(days=1)

    raw = fetch_fmzd(start, end)

    # 使用连接读取一次列集合（避免重复查 information_schema）
    conn = get_db_connection()
    try:
        available_cols = _get_valve_rule_columns(conn)
    finally:
        conn.close()

    mapped = [map_fmzd_item(x, available_cols=available_cols) for x in raw]

    if dry_run:
        samples = []
        for r in mapped[:5]:
            samples.append({k: v for k, v in r.items() if not k.startswith("_")})
        return {
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
            "fetched": len(raw),
            "mapped": len(mapped),
            "dry_run": True,
            "sample": samples,
        }

    result = upsert_valve_rules(mapped)

    _json_log(
        {
            "event": "sync_fmzd_today_done",
            "start": start,
            "end": end,
            "fetched": len(raw),
            "result": result,
        }
    )

    return {
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "fetched": len(raw),
        **result,
    }


@app.post("/optimize", summary="智能体批量优化：规则检索 + 阈值排产")
def optimize_orders(
    items: List[OrderInput],
    operator: str = Query(default="system", description="操作人"),
):
    if not items:
        raise HTTPException(status_code=400, detail="订单列表不能为空")
    result = optimization_service.optimize(items, operator=operator)
    optimization_jobs[result.job.job_id] = result
    _json_log(
        {
            "event": "optimize_done",
            "job_id": result.job.job_id,
            "operator": operator,
            "total": result.job.total,
            "optimized": result.job.optimized,
            "exceptions": result.job.exceptions,
        }
    )
    return result


@app.get("/jobs/{job_id}", summary="查看优化任务状态")
def get_job(job_id: str):
    if job_id not in optimization_jobs:
        raise HTTPException(status_code=404, detail="任务不存在")
    return optimization_jobs[job_id].job


@app.post("/capacity/seed", summary="写入产线阈值样例（用于测试）")
def seed_capacity(record: ThresholdRecord):
    capacity_scheduler.seed_capacity(record)
    return {"status": "ok"}


@app.post("/rollback/{job_id}", summary="回滚任务（示例：仅清理任务缓存）")
def rollback_job(job_id: str):
    if job_id not in optimization_jobs:
        raise HTTPException(status_code=404, detail="任务不存在")
    optimization_jobs.pop(job_id, None)
    return {"status": "rolled_back", "job_id": job_id}


# ================== 本地调试入口 ==================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
