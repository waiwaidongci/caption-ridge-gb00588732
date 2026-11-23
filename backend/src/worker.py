import logging
import time
from pathlib import Path
from src.services.execution import claim, claim_glossary, execute, execute_glossary, materialize_due
from src.utils.wakeup import wait

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
heartbeat = Path('/tmp/analysis-heartbeat')


def main():
    while True:
        try:
            materialize_due()
            lease = claim()
            glossary_lease = None if lease else claim_glossary()
            heartbeat.touch()
            if lease:
                execute(lease)
                heartbeat.touch()
            elif glossary_lease:
                execute_glossary(glossary_lease)
                heartbeat.touch()
            else:
                wait()
        except Exception:
            log.exception('Worker cycle failed')
            time.sleep(2)


if __name__ == '__main__':
    main()
