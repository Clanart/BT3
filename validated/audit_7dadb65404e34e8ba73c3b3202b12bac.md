[1](#0-0) [2](#0-1)

### Citations

**File:** app/src/lib/patch-formatter.ts (L213-220)
```typescript
    patch += formatHunkHeader(
      hunk.header.oldStartLine,
      oldCount,
      hunk.header.newStartLine,
      newCount
    )
    patch += hunkBuf
  })
```

**File:** app/src/lib/diff-parser.ts (L340-344)
```typescript
      // We must increase `diffLineNumber` only when we're certain that the line
      // is not a "no newline" marker. Otherwise, we'll end up with a wrong
      // `diffLineNumber` for the next line. This could happen if the last line
      // in the file doesn't have a newline before the change.
      diffLineNumber++
```
