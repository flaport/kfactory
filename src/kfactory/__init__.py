import rlayout.db as kdb
import rlayout.lay as lay

# rdb module not yet implemented in rlayout
rdb = None

# Add version info (rlayout doesn't expose this yet)
kdb.__version__ = "0.0.1"

# Stub for TileOutputReceiver (not implemented in rlayout - requires GSI callbacks)
class _TileOutputReceiverStub:
    """Stub for TileOutputReceiver - not functional in rlayout."""

    def put(self, ix, iy, tile, region, dbu, clip):
        raise NotImplementedError("TileOutputReceiver not implemented in rlayout")


kdb.TileOutputReceiver = _TileOutputReceiverStub

# Add rotation constant class attributes to Trans (R0, R90, etc.)
# These are prebuilt transformations used as constants in kfactory
kdb.Trans.R0 = kdb.Trans(0)  # No rotation
kdb.Trans.R90 = kdb.Trans(1)  # 90° counterclockwise
kdb.Trans.R180 = kdb.Trans(2)  # 180°
kdb.Trans.R270 = kdb.Trans(3)  # 270° counterclockwise
kdb.Trans.M0 = kdb.Trans(4)  # Mirror at x-axis
kdb.Trans.M45 = kdb.Trans(5)  # Mirror at 45° axis
kdb.Trans.M90 = kdb.Trans(6)  # Mirror at y-axis
kdb.Trans.M135 = kdb.Trans(7)  # Mirror at 135° axis

# Same for DTrans
kdb.DTrans.R0 = kdb.DTrans(0)
kdb.DTrans.R90 = kdb.DTrans(1)
kdb.DTrans.R180 = kdb.DTrans(2)
kdb.DTrans.R270 = kdb.DTrans(3)
kdb.DTrans.M0 = kdb.DTrans(4)
kdb.DTrans.M45 = kdb.DTrans(5)
kdb.DTrans.M90 = kdb.DTrans(6)
kdb.DTrans.M135 = kdb.DTrans(7)

# Same for CplxTrans (complex transformations with magnification)
kdb.CplxTrans.R0 = kdb.CplxTrans(1.0, 0, 0, 0.0, 0.0)
kdb.CplxTrans.R90 = kdb.CplxTrans(1.0, 90, 0, 0.0, 0.0)
kdb.CplxTrans.R180 = kdb.CplxTrans(1.0, 180, 0, 0.0, 0.0)
kdb.CplxTrans.R270 = kdb.CplxTrans(1.0, 270, 0, 0.0, 0.0)
kdb.CplxTrans.M0 = kdb.CplxTrans(1.0, 0, 1, 0.0, 0.0)
kdb.CplxTrans.M45 = kdb.CplxTrans(1.0, 90, 1, 0.0, 0.0)
kdb.CplxTrans.M90 = kdb.CplxTrans(1.0, 180, 1, 0.0, 0.0)
kdb.CplxTrans.M135 = kdb.CplxTrans(1.0, 270, 1, 0.0, 0.0)

# Same for DCplxTrans
kdb.DCplxTrans.R0 = kdb.DCplxTrans(1.0, 0, 0, 0.0, 0.0)
kdb.DCplxTrans.R90 = kdb.DCplxTrans(1.0, 90, 0, 0.0, 0.0)
kdb.DCplxTrans.R180 = kdb.DCplxTrans(1.0, 180, 0, 0.0, 0.0)
kdb.DCplxTrans.R270 = kdb.DCplxTrans(1.0, 270, 0, 0.0, 0.0)
kdb.DCplxTrans.M0 = kdb.DCplxTrans(1.0, 0, 1, 0.0, 0.0)
kdb.DCplxTrans.M45 = kdb.DCplxTrans(1.0, 90, 1, 0.0, 0.0)
kdb.DCplxTrans.M90 = kdb.DCplxTrans(1.0, 180, 1, 0.0, 0.0)
kdb.DCplxTrans.M135 = kdb.DCplxTrans(1.0, 270, 1, 0.0, 0.0)

# Same for ICplxTrans
kdb.ICplxTrans.R0 = kdb.ICplxTrans(1.0, 0, 0, 0, 0)
kdb.ICplxTrans.R90 = kdb.ICplxTrans(1.0, 90, 0, 0, 0)
kdb.ICplxTrans.R180 = kdb.ICplxTrans(1.0, 180, 0, 0, 0)
kdb.ICplxTrans.R270 = kdb.ICplxTrans(1.0, 270, 0, 0, 0)
kdb.ICplxTrans.M0 = kdb.ICplxTrans(1.0, 0, 1, 0, 0)
kdb.ICplxTrans.M45 = kdb.ICplxTrans(1.0, 90, 1, 0, 0)
kdb.ICplxTrans.M90 = kdb.ICplxTrans(1.0, 180, 1, 0, 0)
kdb.ICplxTrans.M135 = kdb.ICplxTrans(1.0, 270, 1, 0, 0)

# Import KCell for use in other modules (must be after kdb modifications)
from .kcell import KCell

__version__ = "0.0.1"
