from __future__ import annotations

import re
from urllib.parse import quote, urlparse, urlunparse

from sqlalchemy import select

from .. import database
from ..database import CandidateRecord, CaseRecord

ALLOWED_HOSTS = {"douyin.com", "www.douyin.com", "v.douyin.com", "iesdouyin.com", "xiaohongshu.com", "www.xiaohongshu.com", "xhslink.com"}


def normalise_url(raw: str) -> tuple[str, str, str | None]:
    value = raw.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("只支持 http/https 链接")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in ALLOWED_HOSTS:
        raise ValueError("链接不是受支持的抖音或小红书地址")
    if host.endswith("douyin.com"):
        source = "douyin"
        match = re.search(r"/(?:video|note)/(\d+)", parsed.path)
    else:
        source = "xiaohongshu"
        match = re.search(r"/(?:explore|discovery/item)/([\w-]+)", parsed.path)
    source_id = match.group(1) if match else None
    canonical = urlunparse(("https", host, parsed.path.rstrip("/"), "", "", ""))
    return canonical, source, source_id


def create_candidates(links: list[str], title: str | None = None, tags: list[str] | None = None) -> dict:
    imported, duplicates, errors = [], [], []
    with database.session() as db:
        for raw in links:
            try:
                canonical, source, source_id = normalise_url(raw)
            except ValueError as exc:
                errors.append({"url": raw, "error": str(exc)})
                continue
            existing = db.scalar(select(CandidateRecord).where(CandidateRecord.canonical_url == canonical))
            if existing:
                duplicates.append(database.candidate_dict(existing))
                continue
            item = CandidateRecord(id=database.new_id("candidate"), source=source, source_id=source_id,
                                   canonical_url=canonical, title=title, tags_json=database.serialise(tags or []))
            db.add(item)
            db.flush()
            imported.append(database.candidate_dict(item))
        db.commit()
    return {"imported": imported, "duplicates": duplicates, "errors": errors}


def get_candidate(candidate_id: str) -> dict | None:
    with database.session() as db:
        item = db.get(CandidateRecord, candidate_id)
        return database.candidate_dict(item) if item else None


def list_candidates(status: str | None = None) -> list[dict]:
    with database.session() as db:
        query = select(CandidateRecord).order_by(CandidateRecord.created_at.desc())
        if status:
            query = query.where(CandidateRecord.status == status)
        return [database.candidate_dict(item) for item in db.scalars(query).all()]


def save_case(payload: dict) -> dict:
    required = str(payload.get("title", "")).strip()
    if not required:
        raise ValueError("案例标题不能为空")
    record = CaseRecord(id=database.new_id("case"), title=required,
                        tags_json=database.serialise(payload.get("tags") or []),
                        shot_notes=payload.get("shot_notes"), outcome=payload.get("outcome", "success"),
                        notes=payload.get("notes"))
    with database.session() as db:
        db.add(record)
        db.commit()
        db.refresh(record)
        return database.case_dict(record)


def list_cases() -> list[dict]:
    with database.session() as db:
        return [database.case_dict(item) for item in db.scalars(select(CaseRecord).order_by(CaseRecord.created_at.desc())).all()]


def review_candidate(candidate_id: str, decision: str, note: str | None = None) -> dict:
    if decision not in {"approved", "rejected", "pending"}:
        raise ValueError("审核结果无效")
    with database.session() as db:
        item = db.get(CandidateRecord, candidate_id)
        if not item:
            raise LookupError("找不到候选视频")
        from ..database import JobRecord
        active = db.scalar(select(JobRecord).where(JobRecord.candidate_id == candidate_id, JobRecord.status.in_(["queued", "running", "defaced", "succeeded"])))
        if active:
            raise RuntimeError("候选已经进入制作，不能修改审核结果")
        item.status, item.review_note = decision, note
        if decision == "approved":
            item.rights_status = "review"
        db.commit()
        return database.candidate_dict(item)


def query_plan(topic: str, platforms: list[str], case_ids: list[str] | None = None) -> dict:
    topic = topic.strip()
    if not topic:
        raise ValueError("搜索主题不能为空")
    cases = list_cases()
    selected = [item for item in cases if not case_ids or item["id"] in case_ids]
    terms = [topic]
    for case in selected:
        terms.extend(case["tags"])
    terms = list(dict.fromkeys(term.strip() for term in terms if term and term.strip()))[:8]
    queries = []
    for term in terms:
        if "douyin" in platforms:
            queries.append({"platform": "douyin", "query": term, "url": f"https://www.douyin.com/search/{quote(term)}"})
        if "xiaohongshu" in platforms:
            queries.append({"platform": "xiaohongshu", "query": term, "url": f"https://www.xiaohongshu.com/search_result?keyword={quote(term)}"})
    return {"topic": topic, "queries": queries, "candidate_count": 0,
            "cases": selected, "sources": {"http": {"status": "not_configured", "message": "等待公司授权数据服务配置"}}}

