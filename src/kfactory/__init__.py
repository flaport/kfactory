import rlayout.db as kdb
import rlayout.lay as lay

# rdb module not yet implemented in rlayout
rdb = None

# Add version info (rlayout doesn't expose this yet)
kdb.__version__ = "0.0.1"

# Alias: rlayout uses LayerProperties for what klayout calls LayerInfo
kdb.LayerInfo = kdb.LayerProperties


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

__version__ = "0.0.1"
