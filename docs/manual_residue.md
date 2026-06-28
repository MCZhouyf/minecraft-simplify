# Signature bootstrap: manual-residue report (paper Sec. 4.3 / App. B.4)

- core primitives in Sigma_MC: **12**
- auto-derived from the observation interface: **8/12** (block_below, held_item, held_tool, inventory_count, sky_exposed, time_of_day, weather, y_level)
- manually added (need world queries beyond flat state, or action context): nearby_block, station_type, station_base_block, ingredient_type
- manual residue (naming + dimension binning + manual declarations): **12 lines** of schema.json
