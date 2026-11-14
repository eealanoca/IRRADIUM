"""One position per process, with the study's unchanged inputs and context."""
import json
import sys
import traceback
from datetime import date
from pathlib import Path

from sim_gamma.workers.monte_carlo_cli import run, write_json


def main():
    request_path, context_path = map(Path, sys.argv[1:3])
    context = json.loads(context_path.read_text())
    try:
        run(request_path, route_context=(
            context['index'], context['count'], context['position'],
            date.fromisoformat(context['activity_date']), context['route'],
        ))
        return 0
    except Exception as error:
        write_json(context_path.with_name('position-error.json'), {
            'error': str(error), 'timeout': isinstance(error, TimeoutError),
        })
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
