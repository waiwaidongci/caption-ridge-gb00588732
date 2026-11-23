import json
from sqlalchemy import func, select
from src.config.database import Session
from src.constants.enums import Role
from src.domain.demo import demo_rows
from src.domain.glossary import parse_coverage
from src.domain.spec import SPEC
from src.models.glossary import GlossaryTerm
from src.models.identity import User
from src.models.source import Dataset
from src.services.datasets import save
from src.services.glossary import bump_version
from src.utils.security import hash_password

# Coverage references the pending orders QJ-N01…N04 of the demo dataset.
_DEMO_TERMS = (
    ("量子退相干", 18, "QJ-N04: 1 2 3 4 5 6 7 8 9 10 11 12"),
    ("非线性光学", 12, "QJ-N04: 10 11 12 13 14 15 16 17 18 19 20 21"),
    ("无障碍叙事", 10, "QJ-N03: 1 2 3 4 5 6 7 8 9"),
    ("口述史访谈伦理", 14, "QJ-N02: 1 2 3 4 5 6 7 8"),
    ("纸张酸化修复", 8, "QJ-N01: 1 2 3 4 5"),
    ("交叉验证", 16, "QJ-N03: 8 9 10 11 12 13 14 15 16\nQJ-N02: 5 6 7 8 9"),
    ("蒙太奇", 9, "QJ-N03: 15 16 17 18 19 20 21 22\nQJ-N01: 4 5 6 7"),
)


def seed():
    with Session.begin() as db:
        for role in Role:
            if not db.get(User, role.value):
                db.add(User(username=role.value, role=role, password_hash=hash_password(f'{role.value}-demo-2611')))
        item, created = save(db, SPEC['default_dataset'], SPEC['sample_name'], demo_rows(), 'seed')
        created_terms = 0
        for text, cost, coverage in _DEMO_TERMS:
            if db.scalar(select(GlossaryTerm).where(GlossaryTerm.term == text)):
                continue
            db.add(GlossaryTerm(term=text, cost=cost, coverage=parse_coverage(coverage), author='seed'))
            created_terms += 1
        if created_terms:
            bump_version(db)
        return {'users': db.scalar(select(func.count()).select_from(User)),
                'datasets': db.scalar(select(func.count()).select_from(Dataset)),
                'seed_dataset_id': item.id, 'row_count': item.row_count, 'checksum': item.checksum,
                'created_dataset': created,
                'glossary_terms': db.scalar(select(func.count()).select_from(GlossaryTerm))}


if __name__ == '__main__':
    print(json.dumps(seed(), ensure_ascii=False))
