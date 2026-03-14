from shipment_sync.carriers.base import CarrierAdapter
from shipment_sync.carriers.cma_cgm import CmaCgmAdapter
from shipment_sync.carriers.cosco import CoscoAdapter
from shipment_sync.carriers.demo import DemoCarrierAdapter
from shipment_sync.carriers.generic_line import GenericLineAdapter
from shipment_sync.carriers.hapag_lloyd import HapagLloydAdapter
from shipment_sync.carriers.maersk import MaerskAdapter
from shipment_sync.carriers.msc import MscAdapter
from shipment_sync.carriers.one import OneAdapter
from shipment_sync.carriers.wan_hai import WanHaiAdapter


def build_carrier_registry() -> dict[str, CarrierAdapter]:
    """
    Map normalized shipping line names to adapters.

    Add real carrier adapters here, for example:
      "maersk": MaerskAdapter(),
      "hapag-lloyd": HapagAdapter(),
    """
    hapag = HapagLloydAdapter()
    maersk = MaerskAdapter()
    one = OneAdapter()
    cosco = CoscoAdapter()
    cma = CmaCgmAdapter()
    msc = MscAdapter()
    pil = GenericLineAdapter(
        env_prefix="PIL",
        line_label="PIL",
        default_page_url_template="https://www.pilship.com/en-our-track-and-trace-p.html",
        challenge_markers=("captcha", "access denied", "forbidden", "please enable javascript"),
    )
    evergreen = GenericLineAdapter(
        env_prefix="EVERGREEN",
        line_label="Evergreen",
        default_page_url_template="https://www.evergreen-line.com/",
        challenge_markers=("captcha", "access denied", "forbidden", "please enable javascript"),
    )
    wan_hai = WanHaiAdapter()
    oocl = GenericLineAdapter(
        env_prefix="OOCL",
        line_label="OOCL",
        default_page_url_template="https://www.oocl.com/eng/ourservices/eservices/cargotracking/Pages/cargotracking.aspx",
        challenge_markers=("captcha", "access denied", "forbidden", "please enable javascript"),
    )
    return {
        "cma cgm": cma,
        "cma-cgm": cma,
        "cma - cgm": cma,
        "cosco": cosco,
        "cosco shipping": cosco,
        "cosco shipping line": cosco,
        "cosco shipping lines": cosco,
        "demo": DemoCarrierAdapter(),
        "hapag lloyd": hapag,
        "hapag-lloyd": hapag,
        "hapag lloyd ag": hapag,
        "msc": msc,
        "msc shipping line": msc,
        "mediterranean shipping company": msc,
        "maersk": maersk,
        "maersk line": maersk,
        "a.p. moller - maersk": maersk,
        "one": one,
        "ocean network express": one,
        "pil": pil,
        "pacific international lines": pil,
        "evergreen": evergreen,
        "evergreen line": evergreen,
        "evergreen marine": evergreen,
        "wan hai": wan_hai,
        "wan hai lines": wan_hai,
        "oocl": oocl,
        "orient overseas container line": oocl,
    }
