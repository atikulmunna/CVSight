# Annotation

The annotation workspaces, the SAM-assisted tools, the SKU catalog, review and sign-off, and the annotation API. Back to the [README](../README.md).

## Annotation canvas

The production canvas uses native SVG overlays over an image layer inside one shared
pan and zoom transform. Every overlay has a constant-width dark halo and a workflow
cue that does not rely on color alone. The box list is virtualized while all overlays
remain present in the SVG for direct hit testing.

Canvas controls:

- `Left Arrow` or `K` selects the previous box in the box list's order.
- `Right Arrow` or `J` selects the next box.
- The box list's **Order** is shelf order, left to right, or **Least confident first**:
  proposals still to decide come first, lowest score first, so the doubtful ones are
  met before the sure ones, and `J` and `K` follow the same order. When proposals are
  scored 90% or more, **Accept N at 90% or more** accepts them all as one undo step.
- Under the canvas, the selected box's shelf row shows as a strip of crops with their
  names, so a facing named differently from its neighbours stands out before it is
  accepted. Clicking a crop selects that box.
- The header shows whether the SAM service answers (`GET /api/segmenter/status`: `ready`,
  `unreachable`, `rejected` when it refuses the token, or `off` when none is configured),
  checked again every minute, so a dropped tunnel shows before a smart click fails.
- `A` accepts, `R` rejects, `F` flags, and `D` duplicates the selected box.
- `Ctrl` plus `Z` undoes the last action and `Ctrl` plus `Shift` plus `Z` (or `Ctrl`
  plus `Y`) redoes it; `Cmd` works the same on macOS, and the tool rail has Undo and
  Redo buttons. Drawing a box that takes the previous SKU, or accepting and naming a box
  in one pick, is one action. Undo never rewrites a saved revision: it saves a new one
  through the same autosave, using reopen to take back an acceptance, reject to remove a
  drawn box, and restore to bring back a rejected one. History lasts until the page is
  reloaded, and choosing the server version in a conflict clears it. Other `Ctrl` and
  `Cmd` combinations go to the browser, so `Ctrl` plus `R` reloads instead of rejecting.
- `Shift`, `Ctrl`, or `Cmd` plus a click on a box, on the canvas or in the list, adds it
  to the selection or takes it out. Dragging across empty canvas with the Select tool
  selects the boxes wholly inside the rectangle, and holding `Shift` adds them to the
  current selection. `Ctrl` plus `A` selects every box. With several boxes selected,
  accept, reject, flag, duplicate, class change, and SKU naming apply to all of them as
  one undo step; moving and resizing need a single selected box.
- `Ctrl` plus `C` copies the selected boxes and `Ctrl` plus `V` pastes them as new
  unreviewed proposals that keep their class and SKU. On the photo they came from they
  land offset by 8 pixels; on another photo they keep their place, so boxes copied on
  one shelf photo carry to the next. The copy lasts until the page reloads.
- `?` opens every shortcut for the current mode; the strip under the canvas keeps only
  the most used ones and a button for the full list.
- The Display panel sets photo brightness and contrast, a fill for boxes, and which
  classes show on the canvas; `O` hides or shows every box. A hidden box still shows
  while it is selected. These settings change nothing in the project and are kept in
  this browser, except hiding every box, which lasts for the current photo.
- The drawing tools show a crosshair at the pointer.
- `M` picks the smart box tool: a click on a product asks SAM 3 for the box around it.
  It needs the SAM service described in [Smart box](#smart-box). In an instance
  segmentation project `M` is the [smart polygon](#smart-polygon) instead.
- In an instance segmentation project `P` draws a product outline in place of `B`. Each
  click adds a point on the photo the first click landed on, and an edge follows the
  pointer from the last one. A click on the first point, or `Enter`, closes the outline
  once it has three points; `Backspace` takes back the last point and `Escape` cancels.
  The finished outline is saved like a drawn box, with its bounds as the box.
- A selected outline shows a handle on every point and a smaller one in the middle of
  every edge, in place of the box handles. Dragging a point moves it, dragging a
  middle handle adds a point there, and `Alt` plus a click removes a point, keeping at
  least three. Dragging inside the outline moves all of it. The box always follows the
  outline's bounds, and each change is one undo step.
- `Alt` plus an arrow key nudges the selected box by one pixel.
- `Alt` plus `Shift` plus an arrow key resizes the selected box by one pixel.
- `+`, `-`, or the mouse wheel changes zoom.
- Hold `Space` and drag, or middle-button drag, to pan.
- `Escape` clears selection and focus dimming.

Every edit is written to local draft storage before synchronization. Autosave can run
after 300 milliseconds, after one second, or manually. Network failures retain the
pending local draft and retry when the browser returns online. An HTTP 409 conflict
shows both the server revision and the local intent, then requires an explicit choice
between keeping the local edit and using the server version.

The Assign SKUs workspace shows only accepted product boxes. It combines the selected
crop with catalog search, recent SKUs, candidate reference images, and an explicit
Unknown / Other action. Number keys `1` through `9` assign visible candidates, `/`
focuses catalog search, `S` repeats the previous assignment, and `U` assigns Unknown /
Other. Assignment advances to the next verified product box and uses the same local
draft, retry, and conflict behavior as box verification.

Canvas timings are measured against the production build, because the development
server serves unminified modules and React double-renders every component under
`StrictMode`. Stage the retained local 354-box fixture and start the preview server
with:

```powershell
./scripts/canvas-check.ps1
```

The script stages the fixture, builds the frontend, prints the processes currently
using the most CPU, then leaves the build served at
`http://127.0.0.1:4173/?fixture=local` and prints the command that stops it. Pass
`-Wait` to hold the terminal until you stop the server with Ctrl+C.
Close other applications, then use the Measure button to collect p50, p95, and maximum
frame, selection, and input timings. The performance panel with that button appears only
with `?fixture=local`, so reviewers working on real photos do not see it.

Every run measures an idle animation loop first and reports it as the browser frame
baseline. That baseline is the control: when the host cannot hold a steady cadence with
no canvas work, or when the canvas measurement comes out faster than the idle loop, the
panel marks the run **not valid** and the timings describe the machine rather than the
canvas. Only a valid run is evidence. The staged real images and fixture JSON remain
local under the ignored `frontend/public/local-fixtures` directory.

## Live annotation workspace

Open an ingested image and its current database-backed annotations with:

```text
http://127.0.0.1:5173/projects/{project_id}/annotate/{image_id}
```

The workspace loads the canonical managed image and current annotation revisions.
Verification, geometry edits, and SKU assignment use the API autosave path. Reloading
the same URL restores server state, with an image-specific local draft used only for
interrupted saves.

Workspace routes are bookmarkable:

- Verify: `/projects/{project_id}/annotate/{image_id}`
- Assign: `/projects/{project_id}/annotate/{image_id}/assign`
- Propagate: `/projects/{project_id}/annotate/{image_id}/propagate`
- Review: `/projects/{project_id}/review`
- Catalog: `/projects/{project_id}/catalog`
- Analytics: `/projects/{project_id}/analytics`

Legacy `?image=`, `?version=`, `?fixture=`, and `?benchmark=` URLs remain supported.

## Smart box

The smart box tool turns a click on a product into a tight box. CVSight sends the click
to `POST /api/images/{image_id}/smart-box`, and the API asks a SAM service for the box
around the object at that point. Nothing is saved until the canvas creates the box
through the annotation API, so it autosaves, takes the previous SKU, and undoes like a
drawn box.

The service is `runtimes/sam3_segmenter`. It runs SAM 3's image model with instance
interactivity, the SAM 1 style click task; SAM 3.1 ships only a video tracking
checkpoint, so `sam3.pt` is the newest checkpoint for single photos. A photo crosses the
network once: the service keeps the encodings of the most recent photos, keyed by
checksum, and the API uploads a photo only when the service does not hold it. Each
click after that runs only SAM's light mask step.

Picking the smart tool sends the open photo ahead of the first click, through
`POST /api/images/{image_id}/smart-prepare`. The API asks the service whether it holds
the photo (`GET /v1/images/{checksum}`) and uploads it only when it does not, so the
first click no longer waits for the upload and SAM's encoding. A click made while that
is still under way waits for it instead of sending the photo again.

Start the service on a machine with a CUDA GPU, such as a cluster node, from a checkout
of this repository. SAM 3 and its checkpoint stay outside the checkout:

```bash
conda create -n sam3 python=3.12 && conda activate sam3
pip install torch==2.10.0 torchvision --index-url https://download.pytorch.org/whl/cu128
git clone https://github.com/facebookresearch/sam3.git ~/sam3 && pip install -e ~/sam3
pip install -r runtimes/sam3_segmenter/requirements.txt
hf auth login        # once Meta has approved access to facebook/sam3
hf download facebook/sam3 sam3.pt --local-dir ~/sam3-checkpoint
export SAM_SEGMENTER_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
echo "$SAM_SEGMENTER_TOKEN"   # CVSight needs the same value
python -m runtimes.sam3_segmenter --checkpoint ~/sam3-checkpoint/sam3.pt --host 0.0.0.0
```

The service listens on port 8093 unless `--port` says otherwise. Every request must
carry the token, because on a shared cluster other users can reach the node's port. The
token is read from the environment, never from the command line, so it stays out of the
process list.

From the PC that runs CVSight, forward a local port to the node through the cluster's
login host, and keep this running while you label:

```bash
ssh -N -L 127.0.0.1:8093:NODE:8093 USER@LOGIN_HOST
```

Some clusters block the login host from reaching ports on the nodes. Then open the
tunnel from the node instead, to a loopback port on the login host, and forward the PC to
that port. With this route the service can listen on `127.0.0.1` alone:

```bash
# on the node
ssh -f -N -R 127.0.0.1:47193:127.0.0.1:8093 USER@LOGIN_HOST
# on the PC
ssh -N -L 127.0.0.1:8093:127.0.0.1:47193 USER@LOGIN_HOST
```

Then point CVSight at the forwarded port in `.env`:

```text
SHELFSIGHT_SEGMENTER_URL=http://127.0.0.1:8093
SHELFSIGHT_SEGMENTER_TOKEN=the value printed above
```

Inside Docker Compose the API container reaches the forward on the host as
`http://host.docker.internal:8093`; set that URL and recreate the `api` service. When
the service is unreachable, the token is wrong, or SAM finds nothing at a point, the
canvas says so and drawing by hand still works. Each click costs one round trip to the
service, so a distant cluster adds its network latency to every box.

## Smart polygon

In an instance segmentation project the M tool is the smart polygon. A click on a
product asks `POST /api/images/{image_id}/smart-polygon` for its outline, and the canvas
shows the outline as a draft with a small panel:

- Click again to add an area SAM missed, or Shift+click something the outline must leave
  out. Each request sends every click so far, up to 16.
- The **Points** slider thins the outline. It opens at the count that keeps every point
  carrying visible shape. Moving it needs no new request, because each point's rank is
  worked out once (Visvalingam's algorithm).
- Enter or **Finish** saves the draft as a product, with the outline and its bounds as
  the box, through the same path as a drawn box: it autosaves, takes the previous SKU,
  and undoes in one step. Esc cancels the draft; switching tools finishes it.

The request takes `points`, each `{"x", "y", "include"}` in canonical image pixels with
at least one included point, and returns `{"polygon", "score"}`. Nothing is saved. The
service answers at `POST /v1/polygon`: SAM predicts a mask from all the clicks (a single
click takes the best of SAM's three masks; more clicks take one mask, as SAM advises),
and OpenCV traces the outer edge of the largest region to within half a pixel, at most
1,000 points.

The polygon route needs OpenCV, which `runtimes/sam3_segmenter/requirements.txt` lists
along with three packages SAM 3 imports without declaring. To update a running service,
pull the checkout, install the requirements again, and restart it. An older service
still answers smart boxes, and the canvas says when it is too old for smart polygons.

Image responses (`GET /api/images/{image_id}` and uploads) carry the project's
`project_type`, which is how the canvas chooses between the smart box and the smart
polygon.

## Find with SAM

**Find with SAM**, in the side panel while verifying, labels a whole shelf at once. It
uses SAM 3's concept prompt: select one or more products as examples (up to 8), type
what to find (such as `bottle`), or both, and choose **Find**. Every match on the photo
that is not labeled yet is added as an unreviewed product proposal, all in one undo
step, and the new proposals are selected: look them over, press `A` to accept them all,
or step through and reject the wrong ones. A find that overlaps a labeled box by half
or more is left out. In an instance segmentation project each proposal keeps SAM's
outline, thinned the way the smart polygon opens; in a detection project it is a box.
SAM's score becomes the proposal's confidence. Examples find products of the same kind,
not the same SKU, so finds start unassigned.

Measured on two labeled shelf photos, a phrase such as `product` finds nearly every
product on the shelf, labeled or not, so it suits projects that label everything. When a
project labels only some brands, select a few of those brands' products as examples
instead. On a very dense shelf (about 170 products) one find caught about a third of
them, with 90% of the finds correct when three examples were selected; finding again
with other examples adds only what is new. Long descriptive phrases did worse than
short ones, and a threshold of 0.3 roughly doubled the finds at much lower precision.

The canvas asks `POST /api/images/{image_id}/smart-find` with `text`, `exemplars` (boxes
in canonical image pixels), and an optional `threshold` (0.05 to 0.95, 0.5 by default),
and receives up to 300 outlines with their scores, best first. Nothing is saved by the
request. The service answers at `POST /v1/find`, adding the prompts to the photo's
cached encoding and removing them again afterwards, so clicks on the same photo are not
affected.

## SKU catalog

One catalog is shared by every project. It stores the product hierarchy as category,
subcategory, brand, and variant, with an optional UPC that is unique across the whole
catalog. Migration `0004_sku_catalog` creates the explicit
`Unknown / Other` target. The unknown target remains active and cannot be edited,
deprecated, or merged.

Catalog routes:

- `GET /api/skus`
- `GET /api/skus/{sku_id}`
- `POST /api/skus`
- `PUT /api/skus/{sku_id}`
- `POST /api/skus/{sku_id}/deprecate`
- `POST /api/skus/{sku_id}/merge`
- `POST /api/skus/{sku_id}/reference-images`
- `GET /api/sku-reference-images/{reference_id}/media/{variant}`

Merge targets must be active. A merge appends auditable annotation revisions that
point current annotations to the target, moves non-duplicate reference images, and
changes the source to `merged` in one database transaction. Reference uploads use the
same size, decoded-pixel, format, orientation, and path validation as dataset images.
Only decoded JPEG and PNG content is accepted.

The web catalog loads up to 2,500 records into a virtualized result list. Search
matches names, UPCs, hierarchy terms, prefixes, small typos, and abbreviations.
Favorites and the 12 most recent selections are stored locally in the browser. If the
catalog API is unavailable, the screen displays a read-only 2,001-SKU fixture for
search and rendering checks.

Owners can optionally attach a CSV catalog while creating a project. The file must be
2 MiB or smaller and contain no more than 2,500 SKU rows. The `name` header is required;
`upc`, `category`, `subcategory`, `brand`, and `variant` are optional. UPC values must
contain 8, 12, 13, or 14 digits and cannot repeat in the file or the shared catalog; a
conflict returns `duplicate_upc` with the clashing values.
Quoted commas and UTF-8 byte-order marks are supported. Project and catalog creation
use one database transaction, so any catalog conflict rolls back the new project and
all rows. `POST /api/datasets` accepts the validated rows in its optional `catalog`
array and returns `imported_skus`. Its `project_type` is `object_detection` (the
default) or `instance_segmentation`, and project summaries return it. Projects created
before project types existed are detection projects.

After creation, the web app opens the new project overview and shows a first-image
checklist. The checklist leads the owner to upload a shelf image, open it, correct and
decide its boxes, assign identities as needed, wait for saved changes, and mark the
image reviewed. Matching guidance on the Images page disappears after a reviewed
image is present.

## Similarity propagation

The Propagate workspace follows the handoff's label-one-confirm-many layout while
keeping every label decision explicit. It uses a separate propagation embedding
purpose and index namespace even when the exploratory T005 CLIP adapter supplies the
vectors. T005 measured only 50.00 percent top-one same-SKU crop retrieval and 13.33
percent hard-pair confusion, so the grid never preselects a crop.

Queue current accepted product crops for asynchronous propagation embeddings:

```text
POST /api/propagation/embeddings
{"annotation_ids":["<annotation-uuid>"]}
```

After the worker completes, create an auditable suggestion set from an exact
human-assigned seed revision:

```text
POST /api/annotations/{annotation_id}/propagation-suggestions
{"expected_revision":3,"top_k":18}
```

Every returned crop starts skipped. The response includes exact cosine scores,
selected and skipped counts, model quality evidence, and any configured SKU hard
pairs. Hard-pair suggestions require an individual acknowledgement.

Confirm or skip every suggestion in one transaction:

```text
POST /api/propagation/suggestion-sets/{suggestion_set_id}/confirm
{"decisions":[{"suggestion_id":"<uuid>","decision":"confirm","reviewed_individually":true}]}
```

The candidate index is scoped to the currently open shelf image, matching the
confirmation-grid workflow and the bounded browser queue. Suggestion rows remain
separate from annotation revisions. Only this actor-authored confirmation action
appends a final `source=propagated` revision. If the seed or any candidate changed,
the whole batch rolls back. Explicit Unknown / Other assignments are never candidates
or propagation seeds.

## Quality review and snapshot sign-off

Open the risk-ranked review workspace for a project with:

```text
http://127.0.0.1:5173/projects/{project_id}/review
```

Select **Review QA**. Each queue item shows every blocking reason and its combined
risk score. The current signals are propagated origin, Unknown or unassigned product
identity, recorded hard-pair review, low model-to-human box agreement, a propagated
label that conflicts with its current seed, and an explicit reviewer flag.

The workspace embeds the same revision-safe annotation canvas used for box correction.
After saving a correction, refresh the queue before deciding. Approve and Flag append a
new annotation revision with reviewer provenance and an immutable review-decision row.
A stale item returns a conflict instead of overwriting the corrected revision.

When the selected item is a product box with an assigned SKU, the panel below the
queue shows a lineup for that SKU: its reference photos, the crop of the box under
review, and every other accepted product box with the same SKU in this version, ordered
by photo and then left to right. A box that does not match its neighbours stands out at
a glance. The lineup lists at most 200 boxes and says when there are more. Each crop is
the box with 8% of shelf context around it, at most 256 pixels on its longest side. A
crop of a named revision never changes, so the browser may keep it for a day; a crop of
the current revision is never cached.

Review routes:

- `GET /api/dataset-versions/{version_id}/review-queue`
- `POST /api/dataset-versions/{version_id}/review-items/{annotation_id}/decision`
- `POST /api/dataset-versions/{version_id}/review-signoff`
- `GET /api/dataset-versions/{version_id}/sku-lineup?sku_id={sku_id}`: the SKU, its
  reference images, and its accepted product boxes in the version, each with a crop URL
- `GET /api/annotations/{annotation_id}/crop?revision={revision}`: a JPEG crop of one
  box; without `revision` it shows the current revision

Sign-off is blocked while any current risk item lacks an approval or has an unresolved
flag. A successful sign-off snapshots the version and records the reviewer in one
database transaction. A version frozen through the general snapshot route before QA
cannot later be assigned a review sign-off.

## Annotation API

Annotation writes derive their author from the authenticated server session. A client
cannot select or override the audit actor. Coordinates are pixels in the canonical
image coordinate system. Boxes must have positive dimensions and remain inside the
canonical image bounds.

The available routes are:

- `GET /api/images/{image_id}/annotations`
- `GET /api/annotations/{annotation_id}`
- `POST /api/images/{image_id}/annotations`
- `PUT /api/annotations/{annotation_id}`
- `POST /api/annotations/{annotation_id}/accept`
- `POST /api/annotations/{annotation_id}/reopen`
- `POST /api/annotations/{annotation_id}/assign-sku`
- `POST /api/annotations/{annotation_id}/reject`
- `POST /api/annotations/{annotation_id}/restore`
- `POST /api/annotations/batch`

`PUT` is a full annotation replacement and requires `expected_revision`. Accept,
reopen, reject, restore, and every batch operation also require the expected current
revision. Accept transitions an active proposal to verified and accepted. Reopen takes a
verified box back to an unreviewed proposal and keeps its geometry and SKU; the canvas
uses it to undo an acceptance. A stale revision
returns HTTP 409 without overwriting newer work. Batch update, reject, and restore
operations run in one transaction, so one failure rolls back the whole batch.

A product in an instance segmentation project may carry a `polygon`: its outline as 3
to 1,000 `[x, y]` points in the same pixel coordinates. The box must be the outline's
bounds, within 0.01 pixels, and the outline must stay inside the image. Gaps, shelf
labels, and detection projects never have an outline; sending one returns
`invalid_polygon`. Because `PUT` replaces the whole annotation, leaving `polygon` out
removes the outline. Every revision keeps the outline it was saved with, and snapshots
(schema `shelfsight-dataset-snapshot/v3`) freeze it with the box. In the canvas, an
outline moves and scales with its box, and changing a product to another class drops
it.

SKU assignment accepts `expected_revision` and `sku_id`. The annotation must be an
accepted product box and the target SKU must be active. Each assignment appends a
revision with `assign_sku` provenance. Earlier model proposal and human verification
revisions remain unchanged, which keeps identity review separate from box provenance.

Domain errors use a stable response shape:

```json
{
  "detail": {
    "code": "stale_revision",
    "message": "annotation changed after the expected revision"
  }
}
```
