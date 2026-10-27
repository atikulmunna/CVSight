# Analytics

Shelf analytics computed from frozen snapshots, so released numbers never change. Back to the [README](../README.md).

## Realogram reconstruction

`benchmark_tool.analytics.reconstruct_realogram` converts verified product facings
into top-to-bottom shelf rows and left-to-right facing sequences. Existing reviewed
`shelf_row_id` values are authoritative. When every row value is absent, the function
clusters boxes by vertical center and marks the result `provisional` until a reviewer
confirms the rows. Unknown SKU identity is preserved and does not affect ordering.

Complete reviewed inputs return `complete`. An incomplete observation returns a
visible `partial` result. A severe oblique view returns `unsupported` with the reason
`severe_oblique_view` instead of producing misleading row order. Mixed manual and
automatic row assignments are rejected. Annotators can correct the selected facing's
shelf row directly in the annotation inspector, using row 0 for the top row.

## Gap and availability analysis

Automatic SAM gap prompting was rejected by the T003 benchmark, so geometry-derived
gaps are review proposals only. `benchmark_tool.gap_analytics.derive_gap_proposals`
finds visible horizontal intervals not occupied by reviewed product facings within a
reviewed shelf-row ROI. It never assigns a SKU. Reviewers can duplicate or reshape a
box, change its class to `Visible gap`, assign its shelf row, and accept it.

`summarize_visible_gaps` computes visible gap fraction from accepted gap annotations
only. Unreviewed or flagged gaps keep the row provisional. Cropped, incomplete, or
occluded observations are partial. `evaluate_gap_predictions` reports precision and
recall only against accepted reviewed gap truth and explicitly reports when no such
truth exists.

`analyze_sku_availability` returns a SKU state with exact observation, expectation,
slot, facing, and gap evidence identifiers. A `shelf_out_of_stock` state requires an
authoritative expectation, complete reviewed observation, reviewed expected slot, and
zero observed facings in the fixture. A visual gap alone returns `not_observed` or
`possible_absence`. Confidence is categorical because no numeric probability has been
calibrated; the response retains `probability: null` and `calibrated: false`.
