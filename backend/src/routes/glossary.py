from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from src.config.database import connection
from src.constants.enums import Role, Status
from src.controllers.auth import allow, current
from src.domain.glossary import parse_coverage
from src.models.glossary import GlossaryPlan, GlossaryTerm
from src.models.identity import User
from src.models.jobs import Run
from src.schemas.requests import GlossaryPlanRequest, TermRequest, TermUpdateRequest
from src.services.audit import record
from src.services.glossary import bump_version, current_version
from src.utils import views
from src.utils.wakeup import wake

router = APIRouter(prefix='/glossary')


@router.get('/terms')
def terms(db: Session = Depends(connection), actor: User = Depends(current)):
    items = db.scalars(select(GlossaryTerm).order_by(GlossaryTerm.id)).all()
    return {'version': current_version(db), 'terms': [views.term(item) for item in items]}


@router.post('/terms', status_code=201)
def create_term(body: TermRequest, db: Session = Depends(connection),
                actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    item = GlossaryTerm(term=body.term, cost=body.cost,
                        coverage=parse_coverage(body.coverage_text), author=actor.username)
    db.add(item)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise HTTPException(409, '该术语已录入，可改为补报其覆盖范围')
    bump_version(db)
    record(db, actor.username, 'glossary.term_created', item.id, term=item.term, positions=len(item.coverage))
    db.commit()
    return views.term(item)


@router.post('/terms/{term_id}')
def update_term(term_id: int, body: TermUpdateRequest, db: Session = Depends(connection),
                actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    # Locking only this term's row: concurrent updates to different terms both commit.
    item = db.get(GlossaryTerm, term_id, with_for_update=True)
    if not item:
        raise HTTPException(404, '找不到该术语')
    changed = False
    if body.term is not None and body.term != item.term:
        item.term = body.term
        changed = True
    if body.cost is not None and abs(body.cost - item.cost) > 1e-6:
        item.cost = body.cost
        changed = True
    if body.coverage_text is not None:
        coverage = parse_coverage(body.coverage_text)
        if coverage != item.coverage:
            item.coverage = coverage
            changed = True
    if not changed:
        return views.term(item)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise HTTPException(409, '该术语已录入，可改为补报其覆盖范围')
    bump_version(db)
    record(db, actor.username, 'glossary.term_updated', item.id, term=item.term, positions=len(item.coverage))
    db.commit()
    return views.term(item)


@router.get('/plans')
def plans(db: Session = Depends(connection), actor: User = Depends(current)):
    return [views.glossary_plan(item) for item in
            db.scalars(select(GlossaryPlan).order_by(GlossaryPlan.id.desc()).limit(50))]


@router.post('/plans', status_code=202)
def create_plan(body: GlossaryPlanRequest, db: Session = Depends(connection),
                actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    run = db.get(Run, body.run_id)
    if not run:
        raise HTTPException(404, '找不到指定的运行记录')
    if run.status != Status.COMPLETE:
        raise HTTPException(409, '请选择已完成的估算运行')
    item = GlossaryPlan(run_id=run.id, budget=body.budget, author=actor.username)
    db.add(item)
    db.flush()
    record(db, actor.username, 'glossary.queued', item.id, run_id=run.id, budget=body.budget)
    db.commit()
    wake()
    return views.glossary_plan(item, detail=True)


@router.get('/plans/{plan_id}')
def plan_detail(plan_id: int, db: Session = Depends(connection), actor: User = Depends(current)):
    item = db.get(GlossaryPlan, plan_id)
    if not item:
        raise HTTPException(404, '找不到该计算记录')
    return views.glossary_plan(item, detail=True)
