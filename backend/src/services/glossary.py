from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from src.models.glossary import GlossaryState, GlossaryTerm


def current_version(db, lock=False):
    state = db.get(GlossaryState, 1, with_for_update=lock)
    return state.version if state else 0


def bump_version(db):
    """Increment the glossary version inside the caller's transaction.

    Term writes and plan publishing both lock this row, so a result is only
    published when the glossary has not changed since the job was claimed.
    """
    state = db.get(GlossaryState, 1, with_for_update=True)
    if state is None:
        try:
            with db.begin_nested():
                state = GlossaryState(id=1, version=0)
                db.add(state)
                db.flush()
        except IntegrityError:
            state = db.get(GlossaryState, 1, with_for_update=True)
    state.version += 1
    db.flush()
    return state.version


def snapshot_terms(db):
    return [{"id": item.id, "term": item.term, "cost": item.cost, "coverage": item.coverage}
            for item in db.scalars(select(GlossaryTerm).order_by(GlossaryTerm.id))]
