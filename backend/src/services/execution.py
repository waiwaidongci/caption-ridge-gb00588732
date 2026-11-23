import logging
from datetime import date, timedelta
from sqlalchemy import and_, or_, select
from src.config.database import Session
from src.constants.enums import Status
from src.domain.analysis import analyze, estimate
from src.domain.glossary import plan_glossary
from src.domain.spec import SPEC
from src.models.glossary import GlossaryPlan, GlossaryState
from src.models.jobs import Delivery, Run, Schedule
from src.models.source import Dataset
from src.services.audit import record
from src.services.glossary import snapshot_terms
from src.utils.exports import write_report
from src.utils.time import now

log = logging.getLogger(__name__)


def materialize_due():
    if not SPEC.get('scheduled'):
        return
    stamp = now()
    with Session.begin() as db:
        schedules = db.scalars(select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_at <= stamp)
                                .order_by(Schedule.next_at).limit(20).with_for_update(skip_locked=True))
        for item in schedules:
            due = item.next_at
            dataset_id = item.dataset_id
            params = dict(item.params)
            if item.repeat_daily:
                elapsed_days = (stamp - due).days
                due += timedelta(days=elapsed_days)
                period_key = SPEC['schedule_date_param']
                report_day = date.fromisoformat(params[period_key]) + timedelta(days=elapsed_days)
                params[period_key] = report_day.isoformat()
                item.params = {**params, period_key: (report_day + timedelta(days=1)).isoformat()}
                dataset_id = db.scalar(select(Dataset.id).order_by(Dataset.id.desc()).limit(1)) or dataset_id
                item.next_at = due + timedelta(days=1)
            else:
                item.enabled = False
            db.add(Run(dataset_id=dataset_id, schedule_id=item.id, scheduled_at=due,
                       name=item.name, params=params, author=item.author))


def claim():
    stamp = now()
    with Session.begin() as db:
        query = select(Run).where(or_(Run.status == Status.QUEUED,
                                      and_(Run.status == Status.RUNNING, Run.lease_until < stamp)))
        job = db.scalar(query.order_by(Run.id).limit(1).with_for_update(skip_locked=True))
        if job is None:
            return None
        if job.attempt >= 3:
            job.status = Status.FAILED
            job.error = '后台进程多次中断，请发起新的运行'
            job.finished_at = stamp
            record(db, 'worker', 'analysis.failed', job.id, reason=job.error)
            return None
        job.status = Status.RUNNING
        job.attempt += 1
        job.lease_until = stamp + timedelta(minutes=3)
        records = db.get(Dataset, job.dataset_id).rows
        return job.id, job.attempt, records, job.params


def execute(lease):
    job_id, attempt, rows, params = lease
    result = export = failure = None
    try:
        result = analyze(rows, params)
        export = write_report(job_id, attempt, result)
    except ValueError as error:
        failure = str(error)[:500]
    except Exception:
        log.exception('Analysis %s failed', job_id)
        failure = '计算失败，请检查字段口径后重试'
    with Session.begin() as db:
        job = db.get(Run, job_id, with_for_update=True)
        if job.attempt != attempt or job.status != Status.RUNNING:
            return
        job.lease_until = None
        job.finished_at = now()
        job.status = Status.FAILED if failure else Status.COMPLETE
        job.result = None if failure else result
        job.export_file = export
        job.error = failure
        record(db, 'worker', f'analysis.{job.status}', job.id, attempt=attempt, reason=failure)
        if job.schedule_id:
            plan = db.get(Schedule, job.schedule_id)
            ready = not failure and result.get('ready', True)
            detail = '已保存 CSV 到站内收件箱' if ready else (failure or result['headline'])
            receipt = Delivery(run_id=job.id, recipient=plan.recipient, state='delivered' if ready else 'blocked', detail=detail[:500])
            db.add(receipt)
            record(db, 'worker', f'delivery.{receipt.state}', job.id, recipient=plan.recipient)


def claim_glossary():
    stamp = now()
    with Session.begin() as db:
        query = select(GlossaryPlan).where(or_(GlossaryPlan.status == Status.QUEUED,
                                               and_(GlossaryPlan.status == Status.RUNNING,
                                                    GlossaryPlan.lease_until < stamp)))
        job = db.scalar(query.order_by(GlossaryPlan.id).limit(1).with_for_update(skip_locked=True))
        if job is None:
            return None
        if job.attempt >= 3:
            job.status = Status.FAILED
            job.error = '后台进程多次中断，请发起新的计算'
            job.finished_at = stamp
            record(db, 'worker', 'glossary.failed', job.id, reason=job.error)
            return None
        job.status = Status.RUNNING
        job.attempt += 1
        job.lease_until = stamp + timedelta(minutes=3)
        # Snapshot the glossary version under its row lock so the terms read below
        # are exactly the state this plan will be validated against at publish time.
        state = db.get(GlossaryState, 1, with_for_update=True)
        job.input_version = state.version if state else 0
        terms = snapshot_terms(db)
        run = db.get(Run, job.run_id)
        records = db.get(Dataset, run.dataset_id).rows
        return job.id, job.attempt, run.name, run.params, records, terms, float(job.budget)


def execute_glossary(lease):
    job_id, attempt, run_name, params, rows, terms, budget = lease
    result = failure = None
    try:
        result = plan_glossary(estimate(rows, params), terms, budget, f'运行「{run_name}」')
    except ValueError as error:
        failure = str(error)[:500]
    except Exception:
        log.exception('Glossary plan %s failed', job_id)
        failure = '计算失败，请检查术语与覆盖范围后重试'
    with Session.begin() as db:
        job = db.get(GlossaryPlan, job_id, with_for_update=True)
        if job.attempt != attempt or job.status != Status.RUNNING:
            return
        state = db.get(GlossaryState, 1, with_for_update=True)
        version = state.version if state else 0
        job.lease_until = None
        job.finished_at = now()
        if version != job.input_version:
            # Coverage moved on while computing: never publish the stale result.
            job.status = Status.STALE
            job.result = None
            job.error = '术语或覆盖范围已更新，本次结果未发布，请重新计算'
        else:
            job.status = Status.FAILED if failure else Status.COMPLETE
            job.result = None if failure else result
            job.error = failure
        record(db, 'worker', f'glossary.{job.status}', job.id, attempt=attempt, reason=job.error)
