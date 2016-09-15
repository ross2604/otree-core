import logging
from django.core.management.base import BaseCommand
import otree.bots.browser
from otree.common_internal import get_redis_conn
import six

# =============================================================================
# LOGGING
# =============================================================================

logger = logging.getLogger('otree.botworker')


# =============================================================================
# COMMAND
# =============================================================================

class Command(BaseCommand):
    help = "oTree: Run the worker for browser bots."

    def add_arguments(self, parser):
        parser.add_argument(
            "--char-range", action="store", type=six.u,
            dest="char_range", default='', help="(Internal)")


    def handle(self, *args, **options):
        char_range = options['char_range']
        redis_conn = get_redis_conn()
        otree.bots.browser.redis_flush_bots(redis_conn, char_range)
        worker = otree.bots.browser.Worker(redis_conn, char_range)
        worker.redis_listen()
