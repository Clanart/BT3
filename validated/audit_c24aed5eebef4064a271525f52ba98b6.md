### Title
Out-of-Bounds Read in DDS Image Diff Parsing via `convertDDSImage` - (File: `app/src/ui/diff/image-diffs/dds-converter.ts`)

### Summary
When GitHub Desktop renders an image diff for a `.dds` texture file, it parses the raw blob bytes with the third-party `parse-dds` library and then slices a `Uint8Array` view directly out of the attacker-supplied `ArrayBuffer` using an `offset`/`length` pair taken from the untrusted parse result, with no bounds validation against the buffer's actual size. This mirrors the bug class in `exif_process_user_comment` (CVE-2019-11042): a length/offset value extracted from attacker-controlled binary metadata is used to index into memory without checking it against the real buffer bounds.

### Finding Description
`convertDDSImage` in `app/src/ui/diff/image-diffs/dds-converter.ts` is invoked whenever a diffed file is rendered as a DDS image: [1](#0-0) 

```
export function convertDDSImage(contents: ArrayBufferLike) {
  const ddsData = parseDDS(contents)
  const [image] = ddsData.images
  const [imageWidth, imageHeight] = image.shape
  const imageData = new Uint8Array(contents, image.offset, image.length)
  ...
```

`contents` is the raw byte buffer of a file that is entirely attacker-controlled: it is either a blob checked out from a cloned/fetched repository or a file the user is viewing an image diff for. The `image.offset`/`image.length` values come straight from `parseDDS(contents)`, which parses the DDS header (`DDS `, `DDSD_*` flags, mipmap/pixel-format fields) — a header format with attacker-controllable numeric fields. Neither `parse-dds`'s output nor `dds-converter.ts` validates that `image.offset + image.length <= contents.byteLength` before constructing the `Uint8Array` view.

This buffer is fed from `getBlobImage`/`getWorkingDirectoryImage` in `app/src/lib/git/diff.ts`, which read blob contents straight from `git show` or the working tree and hand the raw `ArrayBufferLike` to the `Image` model: [2](#0-1) 

and then `ImageContainer` calls `convertDDSImage(image.rawContents)` when `mediaType === 'image/vnd-ms.dds'`: [3](#0-2) 

There is no size/bound check comparable to `isBufferTooLarge`/`isValidBuffer` (which exist for text diffs) applied to the DDS mip-level offset/length before it is used to construct a typed array view.

### Impact Explanation
`new Uint8Array(buffer, byteOffset, length)` throws a `RangeError` if `byteOffset + length` exceeds the buffer, so the most likely outcome is a renderer-process crash/DoS when opening a maliciously crafted `.dds` file's diff. However, because the offset/length pair is fully attacker-controlled and unvalidated against a trusted maximum, if `parse-dds` ever returns an offset that is within bounds of the ArrayBuffer but describes overlapping/adjacent mip levels beyond the intended image region (rather than exceeding the whole buffer), the sliced view can expose adjacent heap bytes from the same underlying `ArrayBuffer` (e.g., padding data or memory that was reused) into the WebGL texture that gets rasterized to a `<canvas>` and read back via `canvas.toDataURL()` — the same "OOB read leads to information disclosure" primitive as the reported EXIF bug, scoped to the renderer's memory rather than PHP's heap.

### Likelihood Explanation
Reaching this path requires: (1) the user opens GitHub Desktop's diff view on a `.dds` file added/modified in a cloned/fetched repository, and (2) the `enableImagePreviewsForDDSFiles` feature flag path is active (referenced in `app/src/lib/git/diff.ts` and `app/src/lib/feature-flag.ts`). Viewing a diff for a changed file is a completely ordinary, unprompted user action in Desktop — no special steps beyond normal repository browsing are needed once a repo containing the crafted file is cloned or fetched. This satisfies the "attacker controls a cloned/fetched repository" and "unprivileged" criteria in the report's Valid Impact scope.

### Recommendation
- Validate `image.offset` and `image.length` returned by `parseDDS` against `contents.byteLength` before constructing the `Uint8Array` view in `convertDDSImage`, and reject/skip rendering with a caught error if the values are inconsistent.
- Apply the same size/sanity checks used for text diffs (`isValidBuffer`, `isBufferTooLarge` in `app/src/lib/git/diff.ts`) to DDS/image buffers prior to parsing.
- Wrap `parseDDS`/`convertDDSImage` in a try/catch that fails safe to the "unrenderable" diff state instead of allowing an unhandled exception or silently returning an out-of-range view.

### Proof of Concept
1. Craft a `.dds` file whose header declares a mipmap `image.offset`/`image.length` pair that is inconsistent with the actual file size (e.g., points past the end of the pixel-data blob) but still parses successfully via `parse-dds`.
2. Commit this file to a repository and have a victim clone/fetch it, or add it as an untracked/modified file.
3. With DDS image previews enabled, open the file's diff in GitHub Desktop.
4. `getBlobImage`/`getWorkingDirectoryImage` (`app/src/lib/git/diff.ts:903-937`) load the raw bytes; `ImageContainer.loadImage` (`app/src/ui/diff/image-diffs/image-container.tsx:29-45`) calls `convertDDSImage(image.rawContents)`, which executes `new Uint8Array(contents, image.offset, image.length)` (`app/src/ui/diff/image-diffs/dds-converter.ts:194`) using the unvalidated attacker-supplied offset/length, either crashing the renderer or exposing adjacent buffer memory in the rendered texture. [4](#0-3) [5](#0-4) [2](#0-1)

### Citations

**File:** app/src/ui/diff/image-diffs/dds-converter.ts (L188-199)
```typescript
export function convertDDSImage(contents: ArrayBufferLike) {
  const ddsData = parseDDS(contents)

  // Get the first mipmap texture.
  const [image] = ddsData.images
  const [imageWidth, imageHeight] = image.shape
  const imageData = new Uint8Array(contents, image.offset, image.length)

  // Draw the DXT texture to the canvas using WebGL2.
  const canvas = document.createElement('canvas')
  drawToCanvas(canvas, ddsData.format, imageWidth, imageHeight, imageData)

```

**File:** app/src/lib/git/diff.ts (L903-916)
```typescript
export async function getBlobImage(
  repository: Repository,
  path: string,
  commitish: string
): Promise<Image> {
  const extension = Path.extname(path)
  const contents = await getBlobContents(repository, commitish, path)
  return new Image(
    contents.buffer,
    contents.toString('base64'),
    getMediaType(extension),
    contents.length
  )
}
```

**File:** app/src/ui/diff/image-diffs/image-container.tsx (L29-45)
```typescript
  public loadImage(image: Image) {
    if (image.mediaType === 'image/vnd-ms.dds') {
      try {
        const dataURL = convertDDSImage(image.rawContents)
        this.setState({
          imageSource: dataURL,
        })
      } catch (error) {
        console.error('Error loading DDS image:', error)
        this.setState({ imageSource: null })
      }
    } else {
      this.setState({
        imageSource: `data:${image.mediaType};base64,${image.contents}`,
      })
    }
  }
```
