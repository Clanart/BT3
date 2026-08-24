## Finding

### Title
Heap memory disclosure via unsliced `Buffer.buffer` passed to DDS image renderer - (File: `app/src/lib/git/diff.ts`)

### Summary
`getBlobImage` and `getWorkingDirectoryImage` in `app/src/lib/git/diff.ts` construct an `Image` model using the raw underlying `ArrayBuffer` of a Node `Buffer` (`contents.buffer`) instead of a buffer sliced to that `Buffer`'s actual `byteOffset`/`length`. That raw `ArrayBuffer` is later indexed with untrusted, attacker-controlled `offset`/`length` values parsed from a `.dds` file by `convertDDSImage` in `app/src/ui/diff/image-diffs/dds-converter.ts`. Because the offsets are computed relative to the *content* start but applied against the *pool*-sized `ArrayBuffer`, the resulting `Uint8Array` view can read bytes that belong to unrelated data sharing Node's internal Buffer memory pool, and that data is subsequently rendered to a `<canvas>` and exposed as a `data:` URL/`<img>` in the renderer DOM. This mirrors the Solidity report's root cause: offset/length values are used to read a data window without validating them against the actual bounds of the intended data — except here the "existing guard" (the `Uint8Array` constructor's own bounds check) is neutralized because the boundary it checks against was already corrupted upstream.

### Finding Description
`getBlobImage`/`getWorkingDirectoryImage`: [1](#0-0) 

Both pass `contents.buffer` — the full backing `ArrayBuffer` of the Node `Buffer`, not a copy or slice bounded to `contents.byteOffset`/`contents.length` — directly into `Image.rawContents`: [2](#0-1) 

Node's `Buffer` allocations for small reads (e.g. `fs.readFile`/`git show` output under the pool threshold) are frequently carved out of a shared internal memory pool; `buffer.buffer` in that case is the *entire pool arraybuffer*, and the real payload starts at `buffer.byteOffset`, not at index 0. Since the pool is shared and reused across unrelated `Buffer` allocations in the process, `contents.buffer` can contain bytes from unrelated data before and after the intended file content.

This `rawContents` is handed to `convertDDSImage`: [3](#0-2) 

`parseDDS(contents)` parses the DDS header/mipmap table from an attacker-supplied `.dds` file (a repository can legitimately contain any bytes named `*.dds`) and returns `image.offset`/`image.length` describing where the compressed texture data sits — measured from the start of the buffer passed in. Because `contents` is the whole pool `ArrayBuffer` rather than a properly bounded slice, these attacker-influenced offsets are applied against the wrong, larger boundary:

```js
const imageData = new Uint8Array(contents, image.offset, image.length)
```

The `Uint8Array` constructor does enforce `offset + length <= buffer.byteLength`, but that check is performed against the pool's full byte length — not against the actual boundaries of the file's own content region. So the "guard" that exists is checking the wrong invariant; it does not, and cannot, stop reads into adjacent, unrelated heap data that happens to reside in the same shared pool.

The resulting bytes are uploaded as a compressed texture (`gl.compressedTexImage2D`) and drawn to an offscreen `<canvas>`, then converted to a `data:image/png` URL via `canvas.toDataURL()` and rendered directly in the DOM: [4](#0-3) 

This is gated only behind the `.dds` image-preview feature flag being enabled, which adds `.dds` to the renderable image extensions: [5](#0-4) 

### Impact Explanation
An attacker who controls a cloned/fetched repository (or a working-tree file the user has, e.g. via a malicious PR checkout) can commit a crafted `.dds` file with a header/mipmap table engineered to reference offsets beyond the intended content region. When the victim opens the file's diff/image preview in Desktop, the app reads that small file into a pooled `Buffer`, hands the *entire pool* to the DDS parser/renderer, and displays whatever adjacent heap bytes land in the computed offset/length window as pixel data — rendered as a visible image and retrievable as a base64 PNG (`canvas.toDataURL()`), or extractable pixel-by-pixel via `getImageData()`. Depending on what else occupies the same Buffer pool at that time in the renderer/main process (other small file reads, blob contents, or other short-lived Buffers), this can leak fragments of unrelated process memory to the DOM, which is visible to the user and to any script with DOM/canvas access (e.g., a compromised or malicious extension, or export/save-image flows).

### Likelihood Explanation
Requires only that the victim open a diff/preview for a `.dds` file supplied by an untrusted repository — no elevated privileges, no local access, and no unnatural user steps beyond normal repository browsing (viewing a changed file's diff, which Desktop does automatically when a file is selected). The `.dds` image preview feature is flag-gated, which limits current exposure, but the code path itself contains no bounds validation tying `image.offset`/`image.length` to the real content region, and `getBlobImage`/`getWorkingDirectoryImage` are core, always-used functions.

### Recommendation
Do not pass the raw `Buffer.buffer` into `Image.rawContents`. Slice to the buffer's actual data window before use, e.g.:

```js
const arrayBuffer = contents.buffer.slice(
  contents.byteOffset,
  contents.byteOffset + contents.length
)
```

Additionally, in `convertDDSImage`, validate that `image.offset + image.length <= contents.byteLength` (and that `image.offset >= 0`) before constructing the `Uint8Array` view, and reject/parse-fail otherwise, mirroring the recommended fix from the report (`require(offset + length <= data.length)`).

### Proof of Concept
1. Enable the DDS image preview feature flag (`enableImagePreviewsForDDSFiles`).
2. Craft a `.dds` file (a handful of bytes, small enough that Node's `fs.readFile`/`git show` returns a pooled `Buffer`) whose header causes `parse-dds` to return an `images[0]` entry with `offset`/`length` referencing bytes past the actual DDS payload but still within a plausible pool-sized `ArrayBuffer` (e.g., `offset` set to a large value, `length` sized to read a full mip level).
3. Commit this file to a repository and have the victim clone/fetch it, then select the file in Desktop's Changes/History view to trigger `getBlobImage`/`getWorkingDirectoryImage` → `convertDDSImage`.
4. Observe that the rendered `<canvas>`/`<img>` (and its `toDataURL()`/`getImageData()` output) contains bytes that do not correspond to the crafted file's declared payload, demonstrating that out-of-window heap bytes from the shared Buffer pool were read and rendered.

### Citations

**File:** app/src/lib/git/diff.ts (L105-107)
```typescript
if (enableImagePreviewsForDDSFiles()) {
  imageFileExtensions.add('.dds')
}
```

**File:** app/src/lib/git/diff.ts (L903-937)
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
/**
 * Retrieve the binary contents of a blob from the working directory
 *
 * Returns an image object containing the base64 encoded string,
 * as <img> tags support the data URI scheme instead of
 * needing to reference a file:// URI
 *
 * https://en.wikipedia.org/wiki/Data_URI_scheme
 */
export async function getWorkingDirectoryImage(
  repository: Repository,
  file: FileChange
): Promise<Image> {
  const contents = await readFile(Path.join(repository.path, file.path))
  return new Image(
    contents.buffer,
    contents.toString('base64'),
    getMediaType(Path.extname(file.path)),
    contents.length
  )
}
```

**File:** app/src/models/diff/image.ts (L1-16)
```typescript
/**
 * A container for holding an image for display in the application
 */
export class Image {
  /**
   * @param contents The base64 encoded contents of the image.
   * @param mediaType The data URI media type, so the browser can render the image correctly.
   * @param bytes Size of the file in bytes.
   */
  public constructor(
    public readonly rawContents: ArrayBufferLike,
    public readonly contents: string,
    public readonly mediaType: string,
    public readonly bytes: number
  ) {}
}
```

**File:** app/src/ui/diff/image-diffs/dds-converter.ts (L188-198)
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
