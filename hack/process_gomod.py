import sys
sys.path.append('../cachito')

from cachi2.core import fetch_gomod_source
from cachi2.core.models import Request


request = Request(
    source_dir="./workdir/sources",
    output_dir="./workdir/output",
)


fetch_gomod_source(request)