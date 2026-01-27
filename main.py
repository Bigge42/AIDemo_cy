"""
软件名称：基于知识检索的排产优化智能体软件 V1.0
软件简称：排产优化智能体
版本号：V1.0
软件类型：应用软件（Web/服务端为主，含前端页面与后端接口）
开发语言与框架：
- 后端：Python（FastAPI/同类框架）、可选 .NET 服务编排
- 检索：Embedding 推理 + FAISS 向量检索
- 前端：Vue/React（订单周期列表页按钮与结果展示）
运行环境：Linux/Windows 服务器，支持 Docker 部署
数据库：支持 SQL Server（订单与排产数据）、MySQL（规则/知识库权威存储，可按实际替换），不强制更换现有数据库
"""
from __future__ import annotations

from typing import Optional, List, Tuple, Set, Dict, Any
from datetime import datetime, date, timedelta
import os
import json
from urllib import request, error
import uuid
import logging
from logging.handlers import TimedRotatingFileHandler

import pymysql
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict


# ================== 软件基本信息 ==================
SOFTWARE_INFO = {
    "software_name": "基于知识检索的排产优化智能体软件 V1.0",
    "software_short_name": "排产优化智能体",
    "version": "V1.0",
    "software_type": "应用软件（Web/服务端为主，含前端页面与后端接口）",
    "languages_frameworks": {
        "backend": "Python（FastAPI/同类框架）、可选 .NET 服务编排",
        "retrieval": "Embedding 推理 + FAISS 向量检索",
        "frontend": "Vue/React（订单周期列表页按钮与结果展示）",
    },
    "runtime": "Linux/Windows 服务器，支持 Docker 部署",
    "databases": "支持 SQL Server（订单与排产数据）、MySQL（规则/知识库权威存储，可按实际替换），不强制更换现有数据库",
    "background": {
        "summary": (
            "制造业排产普遍呈现多品种小批量、需求波动大、交期刚性强、跨部门协同复杂等特点，"
            "传统排产依赖经验与 Excel 手工处理，导致参数口径不一致、阈值约束缺失、知识难沉淀、"
            "批量处理链路长等问题。"
        ),
        "pain_points": [
            "排产参数维护分散，口径不一致、可追溯性弱。",
            "排产日期缺少产线阈值约束，容易超排与频繁改期。",
            "历史订单与经验难以沉淀复用，人员更替导致能力断层。",
            "批量处理耗时长、查询链路多，影响计划响应速度与稳定性。",
        ],
    },
    "goals": [
        "一键批量优化：订单周期列表新增“智能体优化”按钮发起批量计算。",
        "参数智能校准：调用规则服务输出周期基准/固定周期建议并可回写。",
        "阈值约束排产日期：基于产线阈值自动生成 CapacityScheduleDate。",
        "知识沉淀复用：Embedding+FAISS 提升命中率与速度。",
        "可追溯可回滚：输出 trace 与异常清单，支持回滚。",
    ],
    "scope": "订单周期参数维护、排产日期优化、产线阈值约束管理、批量排产优化与复核场景",
    "architecture": [
        "业务系统：订单筛选、发起批量优化任务、结果与异常展示。",
        "智能体服务：批量 Query、Embedding 推理、FAISS 召回、规则校验、阈值排产日期计算与回写审计。",
        "数据层：SQL Server 订单与排产主数据、MySQL 规则/知识库、FAISS 向量索引。",
    ],
    "modules": [
        "批量优化入口与任务管理（按钮触发、任务进度与统计）。",
        "知识检索增强的规则匹配与参数推断（Embedding+FAISS+递进过滤）。",
        "产线阈值约束的排产日期自动优化（容量表+最早可行分配）。",
        "回写、审计、异常清单与回滚（trace 与异常导出）。",
    ],
    "key_technologies": [
        "向量化语义检索（Embedding + FAISS）",
        "批量推断与批量检索（batch embedding + batch search）",
        "规则校验定案（候选集递进过滤）",
        "阈值约束排产日期算法（装箱式最早可行分配）",
    ],
}


class SoftwareInfoResponse(BaseModel):
    software_name: str
    software_short_name: str
    version: str
    software_type: str
    languages_frameworks: Dict[str, str]
    runtime: str
    databases: str
    background: Dict[str, Any]
    goals: List[str]
    scope: str
    architecture: List[str]
    modules: List[str]
    key_technologies: List[str]


# ================== MySQL 配置 ==================
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "rule_user",           # TODO: 改成你的 MySQL 用户名
    "password": "Strong_Pwd_123!", # TODO: 改成你的 MySQL 密码
    "database": "valve_rule_db",   # TODO: 改成你的数据库名
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

DEBUG_TRACE_DEFAULT = os.getenv("DEBUG_TRACE", "0").strip() in ("1", "true", "True", "YES", "yes")

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
if os.getenv("VALVE_RULE_LOG_TO_CONSOLE", "1").strip() in ("1", "true", "True", "YES", "yes"):
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
    fa_men_da_lei:         Optional[str] = None  # 阀门大类
    fa_men_lei_bie:        Optional[str] = None  # 阀门类别
    chan_pin_ming_cheng:   Optional[str] = None  # 产品名称
    gong_cheng_tong_jing:  Optional[str] = None  # 公称通径
    gong_cheng_ya_li:      Optional[str] = None  # 公称压力

    fa_ti_cai_zhi:         Optional[str] = None  # 阀体材质
    nei_jian_cai_zhi:      Optional[str] = None  # 内件材质
    mi_feng_mian_xing_shi: Optional[str] = None  # 密封面形式
    fa_lan_lian_jie:       Optional[str] = None  # 法兰连接方式
    shang_gai_xing_shi:    Optional[str] = None  # 上盖形式
    liu_liang_te_xing:     Optional[str] = None  # 流量特性

    zhi_xing_ji_gou:       Optional[str] = None  # 执行机构
    fu_jian_pei_zhi:       Optional[str] = None  # 附件配置

    wai_gou_fa_ti:         Optional[str] = None  # 外购阀体
    wai_gou_biao_zhi:      Optional[str] = None  # 外购标志
    te_pin:                Optional[str] = None  # 特品
    xu_hao:                Optional[str] = None  # 序号

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
    ["gong_cheng_tong_jing", "gong_cheng_ya_li"],                # 通径/压力
    ["fa_ti_cai_zhi", "nei_jian_cai_zhi"],                       # 材质
    ["te_pin", "wai_gou_biao_zhi", "wai_gou_fa_ti"],             # 特品/外购
    ["zhi_xing_ji_gou", "fu_jian_pei_zhi"],                      # 执行机构/附件
    ["fa_lan_lian_jie", "shang_gai_xing_shi",
     "liu_liang_te_xing", "mi_feng_mian_xing_shi"],              # 结构细节
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


def _outputs_to_preview(outputs: Set[Tuple[Any, Any]], limit: int = 10) -> List[Dict[str, Any]]:
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

        trace.append({
            "group_index": group_idx,
            "group_fields": list(group),
            "added_fields": added_fields,
            "used_fields": list(used_fields),
            "where": " AND ".join(conditions),
            "params": [_truncate(p) for p in params],
            "matched_rule_count": len(rows),
            "distinct_output_count": len(outputs),
            "outputs_preview": _outputs_to_preview(outputs, limit=10),
        })

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
                standard_delivery_date = (
                    data.order_approved_date + timedelta(days=int(cycle))
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

        _json_log({
            "event": "infer_ok",
            "request_id": request_id,
            "id": data.id,
            "started_at": started_at,
            "ended_at": datetime.now(),
            "duration_ms": int((datetime.now() - started_at).total_seconds() * 1000),
            "result": result,
            "trace": trace,  # 日志里永远保留全量 trace（最核心诊断信息）
        })

        resp = {"request_id": request_id, **result}
        if debug_trace:
            resp["trace"] = trace
        return resp

    except HTTPException as ex:
        _json_log({
            "event": "infer_fail",
            "request_id": request_id,
            "id": data.id,
            "started_at": started_at,
            "ended_at": datetime.now(),
            "duration_ms": int((datetime.now() - started_at).total_seconds() * 1000),
            "error_code": ex.status_code,
            "reason": error_reason_from_status(ex.status_code),
            "detail": ex.detail,
            "trace": trace,  # 日志里永远保留全量 trace
        })
        raise ex

    except Exception as e:
        _json_log({
            "event": "infer_fail",
            "request_id": request_id,
            "id": data.id,
            "started_at": started_at,
            "ended_at": datetime.now(),
            "duration_ms": int((datetime.now() - started_at).total_seconds() * 1000),
            "error_code": 500,
            "reason": "SERVER_ERROR",
            "error": str(e),
            "trace": trace,
        })
        raise HTTPException(status_code=500, detail={"message": str(e), "reason": "SERVER_ERROR"})
    finally:
        conn.close()


# ================== FastAPI 初始化 ==================
app = FastAPI(
    title="排产优化智能体",
    description=(
        "基于知识检索的排产优化智能体软件 V1.0：提供规则推断、批量优化、阈值约束排产日期计算等服务。"
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/info", response_model=SoftwareInfoResponse, summary="软件基本信息")
def get_software_info():
    """
    返回排产优化智能体的软件说明与能力概览。
    """
    return SOFTWARE_INFO


# ================== 单条 infer 接口 ==================
@app.post("/infer", summary="单条：推断生产线、固定周期、标准交期和排产日期")
def infer_line_and_cycle(
    data: RuleInput,
    debug_trace: bool = Query(default=DEBUG_TRACE_DEFAULT, description="是否在响应体中返回 trace（用于调试）"),
):
    return do_infer(data, debug_trace=debug_trace)


# ================== 批量 batch_infer 接口（更全面日志） ==================
@app.post("/batch_infer", summary="批量：一次提交多条规则，逐条返回结果并生成日志")
def batch_infer(
    items: List[RuleInput],
    debug_trace: bool = Query(default=DEBUG_TRACE_DEFAULT, description="是否在响应体中返回 trace（用于调试）"),
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

                _json_log({
                    "event": "batch_item_ok",
                    "batch_id": batch_id,
                    "request_id": request_id,
                    "index": idx,
                    "id": item.id,
                    "started_at": item_started,
                    "ended_at": datetime.now(),
                    "duration_ms": int((datetime.now() - item_started).total_seconds() * 1000),
                    "result": r,
                    "trace": trace,
                })

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

                _json_log({
                    "event": "batch_item_fail",
                    "batch_id": batch_id,
                    "request_id": request_id,
                    "index": idx,
                    "id": item.id,
                    "started_at": item_started,
                    "ended_at": datetime.now(),
                    "duration_ms": int((datetime.now() - item_started).total_seconds() * 1000),
                    "error_code": ex.status_code,
                    "reason": reason,
                    "detail": ex.detail,
                    "trace": trace,
                })

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

                _json_log({
                    "event": "batch_item_fail",
                    "batch_id": batch_id,
                    "request_id": request_id,
                    "index": idx,
                    "id": item.id,
                    "started_at": item_started,
                    "ended_at": datetime.now(),
                    "duration_ms": int((datetime.now() - item_started).total_seconds() * 1000),
                    "error_code": 500,
                    "reason": "SERVER_ERROR",
                    "error": str(e),
                    "trace": trace,
                })

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
            _json_log({
                "event": "batch_log_write_fail",
                "batch_id": batch_id,
                "error": str(e),
                "target": log_path,
            })

        # batch 级别结构化日志（app.jsonl）
        _json_log({
            "event": "batch_done",
            "batch_id": batch_id,
            "started_at": started_at,
            "ended_at": datetime.now(),
            "duration_ms": int((datetime.now() - started_at).total_seconds() * 1000),
            "total": len(items),
            "stats": stats,
            "log_file": log_path,
        })

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
        raise HTTPException(status_code=502, detail=f"FMZD接口HTTP错误: {getattr(e, 'code', '')} {err_body[:300]}")
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
            raise HTTPException(status_code=500, detail="valve_rule表未包含任何可写入字段（字段名不匹配）")

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


@app.post("/sync_fmzd_today", summary="同步FMZD：按天增量拉取并写入 valve_rule（start=当天, end=次日）")
def sync_fmzd_today(
    target_date: Optional[str] = Query(default=None, description="要同步哪一天(YYYY-MM-DD)。不填则取服务器当天。"),
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

    _json_log({
        "event": "sync_fmzd_today_done",
        "start": start,
        "end": end,
        "fetched": len(raw),
        "result": result,
    })

    return {
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "fetched": len(raw),
        **result,
    }


# ================== 本地调试入口 ==================

# === 软件说明扩展模块（复杂示例代码与参考数据） ===

class OrderItem(BaseModel):
    order_id: str = Field(..., description="订单号")
    line_code: str = Field(..., description="目标产线")
    product_name: str = Field(..., description="产品名称")
    spec: Optional[str] = Field(default=None, description="规格型号")
    quantity: int = Field(..., ge=1, description="订单数量")
    schedule_date: Optional[date] = Field(default=None, description="目标排产日期")
    requested_date: Optional[date] = Field(default=None, description="客户要求交期")

class JobRequest(BaseModel):
    job_name: str = Field(..., description="任务名称")
    created_by: Optional[str] = Field(default=None, description="发起人")
    items: List[OrderItem] = Field(default_factory=list, description="订单列表")

class JobStatus(BaseModel):
    job_id: str
    job_name: str
    status: str
    created_at: datetime
    updated_at: datetime
    total: int
    success: int
    failed: int

class JobResult(BaseModel):
    job_id: str
    results: List[Dict[str, Any]]

class EmbeddingRequest(BaseModel):
    texts: List[str]
    model: str = "mock-embedding-v1"

class EmbeddingResponse(BaseModel):
    model: str
    vectors: List[List[float]]

class FaissSearchRequest(BaseModel):
    query_vector: List[float]
    top_k: int = 5

class FaissSearchResponse(BaseModel):
    top_k: int
    results: List[Dict[str, Any]]

class ThresholdCalendarEntry(BaseModel):
    line_code: str
    date: date
    current_threshold: int
    current_quantity: int

class CapacityScheduleRequest(BaseModel):
    items: List[OrderItem]
    calendar: List[ThresholdCalendarEntry]
    max_shift_days: int = 30

class CapacityScheduleResult(BaseModel):
    order_id: str
    line_code: str
    assigned_date: date
    shifted_days: int
    reason: Optional[str] = None

JOB_STORE: Dict[str, JobStatus] = {}
JOB_RESULTS: Dict[str, JobResult] = {}
AUDIT_LOGS: List[Dict[str, Any]] = []

def normalize_text(text: str) -> str:
    return " ".join(text.strip().split()).lower()

def build_query_text(item: OrderItem) -> str:
    parts = [
        f"产品名称={item.product_name}",
        f"规格={item.spec or ''}",
        f"产线={item.line_code}",
    ]
    return ";".join([p for p in parts if p])

def simulate_embedding(texts: List[str]) -> List[List[float]]:
    vectors = []
    for t in texts:
        base = sum(ord(c) for c in t) % 997
        vec = [((base + i * 13) % 100) / 100.0 for i in range(16)]
        vectors.append(vec)
    return vectors

def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)

VECTOR_KB: List[Dict[str, Any]] = [
    {"kb_id": "KB-BASE-001", "text": "阀门 大类=调节阀; 产品=闸阀; 通径=DN50", "line": "L1", "cycle": 7},
    {"kb_id": "KB-BASE-002", "text": "阀门 大类=球阀; 产品=截止阀; 通径=DN80", "line": "L2", "cycle": 12},
    {"kb_id": "KB-BASE-003", "text": "阀门 大类=蝶阀; 产品=止回阀; 通径=DN100", "line": "L3", "cycle": 9},
    {"kb_id": "KB-BASE-004", "text": "阀门 大类=闸阀; 产品=闸阀; 通径=DN150", "line": "L4", "cycle": 15},
    {"kb_id": "KB-BASE-005", "text": "阀门 大类=调节阀; 产品=针型阀; 通径=DN25", "line": "L5", "cycle": 5},
]

def simulate_faiss_search(query_vector: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
    kb_vectors = simulate_embedding([normalize_text(x["text"]) for x in VECTOR_KB])
    scored = []
    for kb_item, kb_vec in zip(VECTOR_KB, kb_vectors):
        score = cosine_similarity(query_vector, kb_vec)
        scored.append({"kb_id": kb_item["kb_id"], "score": score, **kb_item})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]

def build_capacity_calendar(calendar: List[ThresholdCalendarEntry]) -> Dict[str, Dict[date, Dict[str, int]]]:
    result: Dict[str, Dict[date, Dict[str, int]]] = {}
    for entry in calendar:
        line_map = result.setdefault(entry.line_code, {})
        line_map[entry.date] = {
            "threshold": entry.current_threshold,
            "quantity": entry.current_quantity,
        }
    return result

def allocate_capacity(items: List[OrderItem], calendar: List[ThresholdCalendarEntry], max_shift_days: int) -> List[CapacityScheduleResult]:
    cap_map = build_capacity_calendar(calendar)
    results: List[CapacityScheduleResult] = []
    grouped: Dict[str, List[OrderItem]] = {}
    for item in items:
        grouped.setdefault(item.line_code, []).append(item)
    for line_code, line_items in grouped.items():
        line_items.sort(key=lambda x: x.schedule_date or date.today())
        for item in line_items:
            target_date = item.schedule_date or date.today()
            assigned = None
            shifted = 0
            for shift in range(max_shift_days + 1):
                check_date = target_date + timedelta(days=shift)
                cap_entry = cap_map.setdefault(line_code, {}).setdefault(check_date, {"threshold": 0, "quantity": 0})
                if cap_entry["threshold"] <= 0:
                    continue
                if cap_entry["quantity"] + item.quantity <= cap_entry["threshold"]:
                    cap_entry["quantity"] += item.quantity
                    assigned = check_date
                    shifted = shift
                    break
            if assigned is None:
                assigned = target_date + timedelta(days=max_shift_days)
                shifted = max_shift_days
                reason = "超过最大顺延窗口，使用默认顺延日期"
            else:
                reason = None
            results.append(CapacityScheduleResult(
                order_id=item.order_id,
                line_code=line_code,
                assigned_date=assigned,
                shifted_days=shifted,
                reason=reason,
            ))
    return results

@app.post("/embedding", response_model=EmbeddingResponse, summary="Embedding 推理模拟")
def embedding_endpoint(payload: EmbeddingRequest):
    vectors = simulate_embedding(payload.texts)
    return EmbeddingResponse(model=payload.model, vectors=vectors)

@app.post("/faiss_search", response_model=FaissSearchResponse, summary="FAISS 检索模拟")
def faiss_search_endpoint(payload: FaissSearchRequest):
    results = simulate_faiss_search(payload.query_vector, payload.top_k)
    return FaissSearchResponse(top_k=payload.top_k, results=results)

@app.post("/jobs", response_model=JobStatus, summary="创建批量优化任务")
def create_job(payload: JobRequest):
    job_id = uuid.uuid4().hex
    now = datetime.now()
    status = JobStatus(
        job_id=job_id,
        job_name=payload.job_name,
        status="created",
        created_at=now,
        updated_at=now,
        total=len(payload.items),
        success=0,
        failed=0,
    )
    JOB_STORE[job_id] = status
    AUDIT_LOGS.append({"event": "job_created", "job_id": job_id, "payload": payload.model_dump()})
    return status

@app.get("/jobs/{job_id}", response_model=JobStatus, summary="查询任务状态")
def get_job(job_id: str):
    status = JOB_STORE.get(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="任务不存在")
    return status

@app.post("/capacity_schedule", response_model=List[CapacityScheduleResult], summary="阈值约束排产日期计算")
def capacity_schedule(payload: CapacityScheduleRequest):
    results = allocate_capacity(payload.items, payload.calendar, payload.max_shift_days)
    AUDIT_LOGS.append({"event": "capacity_schedule", "count": len(results)})
    return results

KNOWLEDGE_BASE_EXAMPLES: List[Dict[str, Any]] = [
    {
        "rule_id": "KB-0001",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN2; 压力=PN2",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0002",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN3; 压力=PN3",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0003",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN4; 压力=PN4",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0004",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN5; 压力=PN5",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0005",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN6; 压力=PN6",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0006",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN7; 压力=PN7",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0007",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN8; 压力=PN8",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0008",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN9; 压力=PN9",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0009",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN10; 压力=PN10",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0010",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN11; 压力=PN11",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0011",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN12; 压力=PN12",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0012",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN13; 压力=PN13",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0013",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN14; 压力=PN14",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0014",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN15; 压力=PN15",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0015",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN16; 压力=PN16",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0016",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN17; 压力=PN17",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0017",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN18; 压力=PN18",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0018",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN19; 压力=PN19",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0019",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN20; 压力=PN20",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0020",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN21; 压力=PN21",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0021",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN22; 压力=PN22",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0022",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN23; 压力=PN23",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0023",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN24; 压力=PN24",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0024",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN25; 压力=PN25",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0025",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN26; 压力=PN26",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0026",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN27; 压力=PN27",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0027",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN28; 压力=PN28",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0028",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN29; 压力=PN29",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0029",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN30; 压力=PN30",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0030",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN31; 压力=PN31",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0031",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN32; 压力=PN32",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0032",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN33; 压力=PN33",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0033",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN34; 压力=PN34",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0034",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN35; 压力=PN35",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0035",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN36; 压力=PN36",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0036",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN37; 压力=PN37",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0037",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN38; 压力=PN38",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0038",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN39; 压力=PN39",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0039",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN40; 压力=PN40",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0040",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN41; 压力=PN1",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0041",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN42; 压力=PN2",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0042",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN43; 压力=PN3",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0043",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN44; 压力=PN4",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0044",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN45; 压力=PN5",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0045",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN46; 压力=PN6",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0046",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN47; 压力=PN7",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0047",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN48; 压力=PN8",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0048",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN49; 压力=PN9",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0049",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN50; 压力=PN10",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0050",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN51; 压力=PN11",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0051",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN52; 压力=PN12",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0052",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN53; 压力=PN13",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0053",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN54; 压力=PN14",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0054",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN55; 压力=PN15",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0055",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN56; 压力=PN16",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0056",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN57; 压力=PN17",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0057",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN58; 压力=PN18",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0058",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN59; 压力=PN19",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0059",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN60; 压力=PN20",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0060",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN61; 压力=PN21",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0061",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN62; 压力=PN22",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0062",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN63; 压力=PN23",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0063",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN64; 压力=PN24",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0064",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN65; 压力=PN25",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0065",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN66; 压力=PN26",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0066",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN67; 压力=PN27",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0067",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN68; 压力=PN28",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0068",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN69; 压力=PN29",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0069",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN70; 压力=PN30",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0070",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN71; 压力=PN31",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0071",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN72; 压力=PN32",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0072",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN73; 压力=PN33",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0073",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN74; 压力=PN34",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0074",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN75; 压力=PN35",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0075",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN76; 压力=PN36",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0076",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN77; 压力=PN37",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0077",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN78; 压力=PN38",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0078",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN79; 压力=PN39",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0079",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN80; 压力=PN40",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0080",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN81; 压力=PN1",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0081",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN82; 压力=PN2",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0082",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN83; 压力=PN3",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0083",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN84; 压力=PN4",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0084",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN85; 压力=PN5",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0085",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN86; 压力=PN6",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0086",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN87; 压力=PN7",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0087",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN88; 压力=PN8",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0088",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN89; 压力=PN9",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0089",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN90; 压力=PN10",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0090",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN91; 压力=PN11",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0091",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN92; 压力=PN12",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0092",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN93; 压力=PN13",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0093",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN94; 压力=PN14",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0094",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN95; 压力=PN15",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0095",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN96; 压力=PN16",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0096",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN97; 压力=PN17",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0097",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN98; 压力=PN18",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0098",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN99; 压力=PN19",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0099",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN100; 压力=PN20",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0100",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN101; 压力=PN21",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0101",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN102; 压力=PN22",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0102",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN103; 压力=PN23",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0103",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN104; 压力=PN24",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0104",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN105; 压力=PN25",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0105",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN106; 压力=PN26",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0106",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN107; 压力=PN27",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0107",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN108; 压力=PN28",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0108",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN109; 压力=PN29",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0109",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN110; 压力=PN30",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0110",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN111; 压力=PN31",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0111",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN112; 压力=PN32",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0112",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN113; 压力=PN33",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0113",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN114; 压力=PN34",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0114",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN115; 压力=PN35",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0115",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN116; 压力=PN36",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0116",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN117; 压力=PN37",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0117",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN118; 压力=PN38",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0118",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN119; 压力=PN39",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0119",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN120; 压力=PN40",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0120",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN121; 压力=PN1",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0121",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN122; 压力=PN2",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0122",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN123; 压力=PN3",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0123",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN124; 压力=PN4",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0124",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN125; 压力=PN5",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0125",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN126; 压力=PN6",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0126",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN127; 压力=PN7",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0127",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN128; 压力=PN8",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0128",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN129; 压力=PN9",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0129",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN130; 压力=PN10",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0130",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN131; 压力=PN11",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0131",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN132; 压力=PN12",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0132",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN133; 压力=PN13",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0133",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN134; 压力=PN14",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0134",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN135; 压力=PN15",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0135",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN136; 压力=PN16",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0136",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN137; 压力=PN17",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0137",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN138; 压力=PN18",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0138",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN139; 压力=PN19",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0139",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN140; 压力=PN20",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0140",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN141; 压力=PN21",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0141",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN142; 压力=PN22",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0142",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN143; 压力=PN23",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0143",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN144; 压力=PN24",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0144",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN145; 压力=PN25",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0145",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN146; 压力=PN26",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0146",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN147; 压力=PN27",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0147",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN148; 压力=PN28",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0148",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN149; 压力=PN29",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0149",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN150; 压力=PN30",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0150",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN151; 压力=PN31",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0151",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN152; 压力=PN32",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0152",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN153; 压力=PN33",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0153",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN154; 压力=PN34",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0154",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN155; 压力=PN35",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0155",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN156; 压力=PN36",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0156",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN157; 压力=PN37",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0157",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN158; 压力=PN38",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0158",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN159; 压力=PN39",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0159",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN160; 压力=PN40",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0160",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN161; 压力=PN1",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0161",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN162; 压力=PN2",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0162",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN163; 压力=PN3",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0163",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN164; 压力=PN4",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0164",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN165; 压力=PN5",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0165",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN166; 压力=PN6",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0166",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN167; 压力=PN7",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0167",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN168; 压力=PN8",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0168",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN169; 压力=PN9",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0169",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN170; 压力=PN10",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0170",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN171; 压力=PN11",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0171",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN172; 压力=PN12",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0172",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN173; 压力=PN13",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0173",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN174; 压力=PN14",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0174",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN175; 压力=PN15",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0175",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN176; 压力=PN16",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0176",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN177; 压力=PN17",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0177",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN178; 压力=PN18",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0178",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN179; 压力=PN19",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0179",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN180; 压力=PN20",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0180",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN181; 压力=PN21",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0181",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN182; 压力=PN22",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0182",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN183; 压力=PN23",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0183",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN184; 压力=PN24",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0184",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN185; 压力=PN25",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0185",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN186; 压力=PN26",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0186",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN187; 压力=PN27",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0187",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN188; 压力=PN28",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0188",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN189; 压力=PN29",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0189",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN190; 压力=PN30",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0190",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN191; 压力=PN31",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0191",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN192; 压力=PN32",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0192",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN193; 压力=PN33",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0193",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN194; 压力=PN34",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0194",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN195; 压力=PN35",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0195",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN196; 压力=PN36",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0196",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN197; 压力=PN37",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0197",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN198; 压力=PN38",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0198",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN199; 压力=PN39",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0199",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN200; 压力=PN40",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0200",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN1; 压力=PN1",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0201",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN2; 压力=PN2",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0202",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN3; 压力=PN3",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0203",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN4; 压力=PN4",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0204",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN5; 压力=PN5",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0205",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN6; 压力=PN6",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0206",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN7; 压力=PN7",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0207",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN8; 压力=PN8",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0208",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN9; 压力=PN9",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0209",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN10; 压力=PN10",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0210",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN11; 压力=PN11",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0211",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN12; 压力=PN12",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0212",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN13; 压力=PN13",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0213",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN14; 压力=PN14",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0214",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN15; 压力=PN15",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0215",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN16; 压力=PN16",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0216",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN17; 压力=PN17",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0217",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN18; 压力=PN18",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0218",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN19; 压力=PN19",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0219",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN20; 压力=PN20",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0220",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN21; 压力=PN21",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0221",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN22; 压力=PN22",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0222",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN23; 压力=PN23",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0223",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN24; 压力=PN24",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0224",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN25; 压力=PN25",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0225",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN26; 压力=PN26",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0226",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN27; 压力=PN27",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0227",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN28; 压力=PN28",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0228",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN29; 压力=PN29",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0229",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN30; 压力=PN30",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0230",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN31; 压力=PN31",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0231",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN32; 压力=PN32",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0232",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN33; 压力=PN33",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0233",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN34; 压力=PN34",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0234",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN35; 压力=PN35",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0235",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN36; 压力=PN36",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0236",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN37; 压力=PN37",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0237",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN38; 压力=PN38",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0238",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN39; 压力=PN39",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0239",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN40; 压力=PN40",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0240",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN41; 压力=PN1",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0241",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN42; 压力=PN2",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0242",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN43; 压力=PN3",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0243",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN44; 压力=PN4",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0244",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN45; 压力=PN5",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0245",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN46; 压力=PN6",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0246",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN47; 压力=PN7",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0247",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN48; 压力=PN8",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0248",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN49; 压力=PN9",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0249",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN50; 压力=PN10",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0250",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN51; 压力=PN11",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0251",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN52; 压力=PN12",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0252",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN53; 压力=PN13",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0253",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN54; 压力=PN14",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0254",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN55; 压力=PN15",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0255",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN56; 压力=PN16",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0256",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN57; 压力=PN17",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0257",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN58; 压力=PN18",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0258",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN59; 压力=PN19",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0259",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN60; 压力=PN20",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0260",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN61; 压力=PN21",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0261",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN62; 压力=PN22",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0262",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN63; 压力=PN23",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0263",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN64; 压力=PN24",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0264",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN65; 压力=PN25",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0265",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN66; 压力=PN26",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0266",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN67; 压力=PN27",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0267",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN68; 压力=PN28",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0268",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN69; 压力=PN29",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0269",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN70; 压力=PN30",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0270",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN71; 压力=PN31",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0271",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN72; 压力=PN32",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0272",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN73; 压力=PN33",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0273",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN74; 压力=PN34",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0274",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN75; 压力=PN35",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0275",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN76; 压力=PN36",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0276",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN77; 压力=PN37",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0277",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN78; 压力=PN38",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0278",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN79; 压力=PN39",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0279",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN80; 压力=PN40",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0280",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN81; 压力=PN1",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0281",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN82; 压力=PN2",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0282",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN83; 压力=PN3",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0283",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN84; 压力=PN4",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0284",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN85; 压力=PN5",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0285",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN86; 压力=PN6",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0286",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN87; 压力=PN7",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0287",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN88; 压力=PN8",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0288",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN89; 压力=PN9",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0289",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN90; 压力=PN10",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0290",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN91; 压力=PN11",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0291",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN92; 压力=PN12",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0292",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN93; 压力=PN13",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0293",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN94; 压力=PN14",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0294",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN95; 压力=PN15",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0295",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN96; 压力=PN16",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0296",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN97; 压力=PN17",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0297",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN98; 压力=PN18",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0298",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN99; 压力=PN19",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0299",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN100; 压力=PN20",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0300",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN101; 压力=PN21",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0301",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN102; 压力=PN22",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0302",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN103; 压力=PN23",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0303",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN104; 压力=PN24",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0304",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN105; 压力=PN25",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0305",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN106; 压力=PN26",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0306",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN107; 压力=PN27",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0307",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN108; 压力=PN28",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0308",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN109; 压力=PN29",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0309",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN110; 压力=PN30",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0310",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN111; 压力=PN31",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0311",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN112; 压力=PN32",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0312",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN113; 压力=PN33",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0313",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN114; 压力=PN34",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0314",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN115; 压力=PN35",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0315",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN116; 压力=PN36",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0316",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN117; 压力=PN37",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0317",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN118; 压力=PN38",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0318",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN119; 压力=PN39",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0319",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN120; 压力=PN40",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0320",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN121; 压力=PN1",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0321",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN122; 压力=PN2",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0322",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN123; 压力=PN3",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0323",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN124; 压力=PN4",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0324",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN125; 压力=PN5",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0325",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN126; 压力=PN6",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0326",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN127; 压力=PN7",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0327",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN128; 压力=PN8",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0328",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN129; 压力=PN9",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0329",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN130; 压力=PN10",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0330",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN131; 压力=PN11",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0331",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN132; 压力=PN12",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0332",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN133; 压力=PN13",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0333",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN134; 压力=PN14",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0334",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN135; 压力=PN15",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0335",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN136; 压力=PN16",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0336",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN137; 压力=PN17",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0337",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN138; 压力=PN18",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0338",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN139; 压力=PN19",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0339",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN140; 压力=PN20",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0340",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN141; 压力=PN21",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0341",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN142; 压力=PN22",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0342",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN143; 压力=PN23",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0343",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN144; 压力=PN24",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0344",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN145; 压力=PN25",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0345",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN146; 压力=PN26",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0346",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN147; 压力=PN27",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0347",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN148; 压力=PN28",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0348",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN149; 压力=PN29",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0349",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN150; 压力=PN30",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0350",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN151; 压力=PN31",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0351",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN152; 压力=PN32",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0352",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN153; 压力=PN33",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0353",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN154; 压力=PN34",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0354",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN155; 压力=PN35",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0355",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN156; 压力=PN36",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0356",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN157; 压力=PN37",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0357",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN158; 压力=PN38",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0358",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN159; 压力=PN39",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0359",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN160; 压力=PN40",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0360",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN161; 压力=PN1",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0361",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN162; 压力=PN2",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0362",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN163; 压力=PN3",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0363",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN164; 压力=PN4",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0364",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN165; 压力=PN5",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0365",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN166; 压力=PN6",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0366",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN167; 压力=PN7",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0367",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN168; 压力=PN8",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0368",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN169; 压力=PN9",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0369",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN170; 压力=PN10",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0370",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN171; 压力=PN11",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0371",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN172; 压力=PN12",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0372",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN173; 压力=PN13",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0373",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN174; 压力=PN14",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0374",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN175; 压力=PN15",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0375",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN176; 压力=PN16",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0376",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN177; 压力=PN17",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0377",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN178; 压力=PN18",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0378",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN179; 压力=PN19",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0379",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN180; 压力=PN20",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0380",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN181; 压力=PN21",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0381",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN182; 压力=PN22",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0382",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN183; 压力=PN23",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0383",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN184; 压力=PN24",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0384",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN185; 压力=PN25",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0385",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN186; 压力=PN26",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0386",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN187; 压力=PN27",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0387",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN188; 压力=PN28",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0388",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN189; 压力=PN29",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0389",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN190; 压力=PN30",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0390",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN191; 压力=PN31",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0391",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN192; 压力=PN32",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0392",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN193; 压力=PN33",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0393",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN194; 压力=PN34",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0394",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN195; 压力=PN35",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0395",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN196; 压力=PN36",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0396",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN197; 压力=PN37",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0397",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN198; 压力=PN38",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0398",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN199; 压力=PN39",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0399",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN200; 压力=PN40",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0400",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN1; 压力=PN1",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0401",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN2; 压力=PN2",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0402",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN3; 压力=PN3",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0403",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN4; 压力=PN4",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0404",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN5; 压力=PN5",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0405",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN6; 压力=PN6",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0406",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN7; 压力=PN7",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0407",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN8; 压力=PN8",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0408",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN9; 压力=PN9",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0409",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN10; 压力=PN10",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0410",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN11; 压力=PN11",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0411",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN12; 压力=PN12",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0412",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN13; 压力=PN13",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0413",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN14; 压力=PN14",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0414",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN15; 压力=PN15",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0415",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN16; 压力=PN16",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0416",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN17; 压力=PN17",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0417",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN18; 压力=PN18",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0418",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN19; 压力=PN19",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0419",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN20; 压力=PN20",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0420",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN21; 压力=PN21",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0421",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN22; 压力=PN22",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0422",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN23; 压力=PN23",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0423",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN24; 压力=PN24",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0424",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN25; 压力=PN25",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0425",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN26; 压力=PN26",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0426",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN27; 压力=PN27",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0427",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN28; 压力=PN28",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0428",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN29; 压力=PN29",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0429",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN30; 压力=PN30",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0430",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN31; 压力=PN31",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0431",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN32; 压力=PN32",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0432",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN33; 压力=PN33",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0433",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN34; 压力=PN34",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0434",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN35; 压力=PN35",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0435",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN36; 压力=PN36",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0436",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN37; 压力=PN37",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0437",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN38; 压力=PN38",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0438",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN39; 压力=PN39",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0439",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN40; 压力=PN40",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0440",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN41; 压力=PN1",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0441",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN42; 压力=PN2",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0442",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN43; 压力=PN3",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0443",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN44; 压力=PN4",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0444",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN45; 压力=PN5",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0445",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN46; 压力=PN6",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0446",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN47; 压力=PN7",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0447",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN48; 压力=PN8",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0448",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN49; 压力=PN9",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0449",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN50; 压力=PN10",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0450",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN51; 压力=PN11",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0451",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN52; 压力=PN12",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0452",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN53; 压力=PN13",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0453",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN54; 压力=PN14",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0454",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN55; 压力=PN15",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0455",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN56; 压力=PN16",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0456",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN57; 压力=PN17",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0457",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN58; 压力=PN18",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0458",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN59; 压力=PN19",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0459",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN60; 压力=PN20",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0460",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN61; 压力=PN21",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0461",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN62; 压力=PN22",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0462",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN63; 压力=PN23",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0463",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN64; 压力=PN24",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0464",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN65; 压力=PN25",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0465",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN66; 压力=PN26",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0466",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN67; 压力=PN27",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0467",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN68; 压力=PN28",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0468",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN69; 压力=PN29",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0469",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN70; 压力=PN30",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0470",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN71; 压力=PN31",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0471",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN72; 压力=PN32",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0472",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN73; 压力=PN33",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0473",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN74; 压力=PN34",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0474",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN75; 压力=PN35",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0475",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN76; 压力=PN36",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0476",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN77; 压力=PN37",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0477",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN78; 压力=PN38",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0478",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN79; 压力=PN39",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0479",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN80; 压力=PN40",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0480",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN81; 压力=PN1",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0481",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN82; 压力=PN2",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0482",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN83; 压力=PN3",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0483",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN84; 压力=PN4",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0484",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN85; 压力=PN5",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0485",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN86; 压力=PN6",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0486",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN87; 压力=PN7",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0487",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN88; 压力=PN8",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0488",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN89; 压力=PN9",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0489",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN90; 压力=PN10",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0490",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN91; 压力=PN11",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0491",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN92; 压力=PN12",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0492",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN93; 压力=PN13",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0493",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN94; 压力=PN14",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0494",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN95; 压力=PN15",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0495",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN96; 压力=PN16",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0496",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN97; 压力=PN17",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0497",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN98; 压力=PN18",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0498",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN99; 压力=PN19",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0499",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN100; 压力=PN20",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0500",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN101; 压力=PN21",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0501",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN102; 压力=PN22",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0502",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN103; 压力=PN23",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0503",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN104; 压力=PN24",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0504",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN105; 压力=PN25",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0505",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN106; 压力=PN26",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0506",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN107; 压力=PN27",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0507",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN108; 压力=PN28",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0508",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN109; 压力=PN29",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0509",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN110; 压力=PN30",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0510",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN111; 压力=PN31",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0511",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN112; 压力=PN32",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0512",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN113; 压力=PN33",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0513",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN114; 压力=PN34",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0514",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN115; 压力=PN35",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0515",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN116; 压力=PN36",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0516",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN117; 压力=PN37",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0517",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN118; 压力=PN38",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0518",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN119; 压力=PN39",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0519",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN120; 压力=PN40",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0520",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN121; 压力=PN1",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0521",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN122; 压力=PN2",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0522",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN123; 压力=PN3",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0523",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN124; 压力=PN4",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0524",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN125; 压力=PN5",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0525",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN126; 压力=PN6",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0526",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN127; 压力=PN7",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0527",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN128; 压力=PN8",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0528",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN129; 压力=PN9",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0529",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN130; 压力=PN10",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0530",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN131; 压力=PN11",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0531",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN132; 压力=PN12",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0532",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN133; 压力=PN13",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0533",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN134; 压力=PN14",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0534",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN135; 压力=PN15",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0535",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN136; 压力=PN16",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0536",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN137; 压力=PN17",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0537",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN138; 压力=PN18",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0538",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN139; 压力=PN19",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0539",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN140; 压力=PN20",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0540",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN141; 压力=PN21",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0541",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN142; 压力=PN22",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0542",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN143; 压力=PN23",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0543",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN144; 压力=PN24",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0544",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN145; 压力=PN25",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0545",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN146; 压力=PN26",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0546",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN147; 压力=PN27",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0547",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN148; 压力=PN28",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0548",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN149; 压力=PN29",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0549",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN150; 压力=PN30",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0550",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN151; 压力=PN31",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0551",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN152; 压力=PN32",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0552",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN153; 压力=PN33",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0553",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN154; 压力=PN34",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0554",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN155; 压力=PN35",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0555",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN156; 压力=PN36",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0556",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN157; 压力=PN37",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0557",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN158; 压力=PN38",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0558",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN159; 压力=PN39",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0559",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN160; 压力=PN40",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0560",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN161; 压力=PN1",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0561",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN162; 压力=PN2",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0562",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN163; 压力=PN3",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0563",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN164; 压力=PN4",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0564",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN165; 压力=PN5",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0565",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN166; 压力=PN6",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0566",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN167; 压力=PN7",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0567",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN168; 压力=PN8",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0568",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN169; 压力=PN9",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0569",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN170; 压力=PN10",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0570",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN171; 压力=PN11",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0571",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN172; 压力=PN12",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 2,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0572",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN173; 压力=PN13",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 3,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0573",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN174; 压力=PN14",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 4,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0574",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN175; 压力=PN15",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 5,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0575",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN176; 压力=PN16",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 6,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0576",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN177; 压力=PN17",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 7,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0577",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN178; 压力=PN18",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 8,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0578",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN179; 压力=PN19",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 9,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0579",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN180; 压力=PN20",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 10,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0580",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN181; 压力=PN21",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 11,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0581",
        "query": "阀门大类=示例大类1; 产品=示例产品1; 通径=DN182; 压力=PN22",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 12,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0582",
        "query": "阀门大类=示例大类2; 产品=示例产品2; 通径=DN183; 压力=PN23",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 13,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0583",
        "query": "阀门大类=示例大类3; 产品=示例产品3; 通径=DN184; 压力=PN24",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 14,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0584",
        "query": "阀门大类=示例大类4; 产品=示例产品4; 通径=DN185; 压力=PN25",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 15,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0585",
        "query": "阀门大类=示例大类0; 产品=示例产品5; 通径=DN186; 压力=PN26",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 16,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0586",
        "query": "阀门大类=示例大类1; 产品=示例产品6; 通径=DN187; 压力=PN27",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 17,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0587",
        "query": "阀门大类=示例大类2; 产品=示例产品7; 通径=DN188; 压力=PN28",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 18,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0588",
        "query": "阀门大类=示例大类3; 产品=示例产品8; 通径=DN189; 压力=PN29",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 19,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0589",
        "query": "阀门大类=示例大类4; 产品=示例产品9; 通径=DN190; 压力=PN30",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 20,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0590",
        "query": "阀门大类=示例大类0; 产品=示例产品10; 通径=DN191; 压力=PN31",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 21,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
    {
        "rule_id": "KB-0591",
        "query": "阀门大类=示例大类1; 产品=示例产品11; 通径=DN192; 压力=PN32",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 22,
        },
        "tags": ["排产", "知识库", "示例1"],
    },
    {
        "rule_id": "KB-0592",
        "query": "阀门大类=示例大类2; 产品=示例产品12; 通径=DN193; 压力=PN33",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 23,
        },
        "tags": ["排产", "知识库", "示例2"],
    },
    {
        "rule_id": "KB-0593",
        "query": "阀门大类=示例大类3; 产品=示例产品13; 通径=DN194; 压力=PN34",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 24,
        },
        "tags": ["排产", "知识库", "示例3"],
    },
    {
        "rule_id": "KB-0594",
        "query": "阀门大类=示例大类4; 产品=示例产品14; 通径=DN195; 压力=PN35",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 25,
        },
        "tags": ["排产", "知识库", "示例4"],
    },
    {
        "rule_id": "KB-0595",
        "query": "阀门大类=示例大类0; 产品=示例产品15; 通径=DN196; 压力=PN36",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 26,
        },
        "tags": ["排产", "知识库", "示例5"],
    },
    {
        "rule_id": "KB-0596",
        "query": "阀门大类=示例大类1; 产品=示例产品16; 通径=DN197; 压力=PN37",
        "output": {
            "sheng_chan_xian": "L2",
            "gu_ding_zhou_qi": 27,
        },
        "tags": ["排产", "知识库", "示例6"],
    },
    {
        "rule_id": "KB-0597",
        "query": "阀门大类=示例大类2; 产品=示例产品17; 通径=DN198; 压力=PN38",
        "output": {
            "sheng_chan_xian": "L3",
            "gu_ding_zhou_qi": 28,
        },
        "tags": ["排产", "知识库", "示例7"],
    },
    {
        "rule_id": "KB-0598",
        "query": "阀门大类=示例大类3; 产品=示例产品18; 通径=DN199; 压力=PN39",
        "output": {
            "sheng_chan_xian": "L4",
            "gu_ding_zhou_qi": 29,
        },
        "tags": ["排产", "知识库", "示例8"],
    },
    {
        "rule_id": "KB-0599",
        "query": "阀门大类=示例大类4; 产品=示例产品19; 通径=DN200; 压力=PN40",
        "output": {
            "sheng_chan_xian": "L5",
            "gu_ding_zhou_qi": 30,
        },
        "tags": ["排产", "知识库", "示例9"],
    },
    {
        "rule_id": "KB-0600",
        "query": "阀门大类=示例大类0; 产品=示例产品0; 通径=DN1; 压力=PN1",
        "output": {
            "sheng_chan_xian": "L1",
            "gu_ding_zhou_qi": 1,
        },
        "tags": ["排产", "知识库", "示例0"],
    },
]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
