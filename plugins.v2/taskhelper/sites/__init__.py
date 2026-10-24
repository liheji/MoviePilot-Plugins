from .base import NexusTaskSite
from .carpt import CarPtTaskSite
from .crabpt import CrabPtTaskSite
from .hitpt import HitPtTaskSite
from .novahd import NovaHdTaskSite
from .ptskit import PtsKitTaskSite
from .tangping import TangPingTaskSite
from .zmpt import ZmptTaskSite


TASK_SITE_HANDLERS = (
    TangPingTaskSite,
    HitPtTaskSite,
    PtsKitTaskSite,
    CarPtTaskSite,
    NovaHdTaskSite,
    ZmptTaskSite,
    CrabPtTaskSite,
)

__all__ = ["NexusTaskSite", "TASK_SITE_HANDLERS"]
