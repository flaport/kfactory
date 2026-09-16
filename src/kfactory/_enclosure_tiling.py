"""Native receiver implementations loaded only by tiled enclosure operations."""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, overload

from . import kdb

if TYPE_CHECKING:
    from .kcell import KCell


class RegionOperator(kdb.TileOutputReceiver):
    """Region collector. Just getst the tile and inserts it into the target cell."""

    def __init__(self, cell: KCell, layer: kdb.LayerInfo) -> None:
        """Initialization.

        Args:
            cell: Target cell.
            layer: Target layer.
        """
        self.kcell = cell
        self.layer = layer
        self.region = kdb.Region()

    def put(
        self,
        ix: int,
        iy: int,
        tile: kdb.Box,
        region: kdb.Region,
        dbu: float,
        clip: bool,
    ) -> None:
        """Tiling Processor output call.

        Args:
            ix: x-axis index of tile.
            iy: y_axis index of tile.
            tile: The bounding box of the tile.
            region: The target object of the `klayout.db.TilingProcessor`
            dbu: dbu used by the processor.
            clip: Whether the target was clipped to the tile or not.
        """
        self.region.insert(region)

    @overload
    def insert(self) -> None: ...

    @overload
    def insert(self, port_holes: kdb.Region) -> None: ...

    def insert(
        self,
        port_holes: kdb.Region | None = None,
    ) -> None:
        """Insert the finished region into the cell.

        Args:
            port_holes: Carve out holes around the ports.
        """
        if port_holes:
            self.region -= port_holes
        self.kcell.shapes(self.layer).insert(self.region)

class RegionTilesOperator(kdb.TileOutputReceiver):
    """Region collector. Just getst the tile and inserts it into the target cell.

    As it can be used multiple times for the same tile, it needs to merge when
    inserting.
    """

    def __init__(
        self,
        cell: KCell,
        layers: list[kdb.LayerInfo],
    ) -> None:
        """Initialization.

        Args:
            cell: Target cell.
            layers: Target layers.
        """
        self.kcell = cell
        self.layers = layers
        self.merged_region: kdb.Region = kdb.Region()
        self.merged = False
        self.regions: dict[int, dict[int, kdb.Region]] = defaultdict(
            lambda: defaultdict(kdb.Region)
        )

    def put(
        self,
        ix: int,
        iy: int,
        tile: kdb.Box,
        region: kdb.Region,
        dbu: float,
        clip: bool,
    ) -> None:
        """Tiling Processor output call.

        Args:
            ix: x-axis index of tile.
            iy: y_axis index of tile.
            tile: The bounding box of the tile.
            region: The target object of the `klayout.db.TilingProcessor`
            dbu: dbu used by the processor.
            clip: Whether the target was clipped to the tile or not.
        """
        self.regions[ix][iy].insert(region)

    def merge_region(self) -> None:
        """Create one region from the individual tiles."""
        for dicts in self.regions.values():
            for reg in dicts.values():
                self.merged_region.insert(reg)

    @overload
    def insert(self) -> None: ...

    @overload
    def insert(self, port_hole_map: dict[kdb.LayerInfo, kdb.Region]) -> None: ...

    def insert(
        self,
        port_hole_map: dict[kdb.LayerInfo, kdb.Region] | None = None,
    ) -> None:
        """Insert the finished region into the cell.

        Args:
            port_hole_map: Carve out holes around the ports.
        """
        if not self.merged:
            self.merge_region()

        if port_hole_map:
            for layer in self.layers:
                self.merged_region -= port_hole_map[layer]
            self.kcell.shapes(layer).insert(self.merged_region)
        else:
            for layer in self.layers:
                self.kcell.shapes(layer).insert(self.merged_region)
