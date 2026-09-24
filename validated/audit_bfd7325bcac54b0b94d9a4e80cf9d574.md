### Title
Protobuf C++ JSON Writer's Security-Escape Deny List Is Underinclusive: BiDi Isolate Marks (U+2065–U+2069) Are Not Escaped, Enabling Visual Spoofing in Downstream JSON Consumers - (File: src/google/protobuf/json/internal/writer.cc)

### Summary
The C++ Protobuf JSON writer's `MustEscape()` function contains a deny list of Unicode code-point ranges (`kEscapedRanges`) that is explicitly documented as existing "to prevent security bugs in JavaScript" (i.e., a visual-spoofing/XSS-adjacent defense analogous to Symfony's BiDi deny-list). This list omits the Unicode "isolate" BiDi formatting characters U+2066 (LRI), U+2067 (RLI), U+2068 (FSI), and U+2069 (PDI) — the same code points explicitly named in the Symfony advisory as a defense-relevant BiDi range. A protobuf `string` field containing these characters is emitted **unescaped** (as raw UTF-8) into JSON output produced by `google::protobuf::json_internal::JsonWriter`, unlike the semantically adjacent ranges immediately before and after it, which ARE escaped.

### Finding Description
`JsonWriter::WriteEscapedUtf8()` decodes each UTF-8 scalar from the string field via `ConsumeUtf8Scalar()` and calls `MustEscape(scalar.u32, ...)` to decide whether to `\u`-escape it [1](#0-0) .

`MustEscape()`'s comment states the purpose explicitly: "These are not required by the JSON spec, but help to prevent security bugs in JavaScript... originally present in the ESF parser" [2](#0-1) . The deny-list ranges are:

```
{0x0000, 0x001f}, {0x007f, 0x009f}, {0x0600, 0x0603},
{0x200b, 0x200f}, {0x2028, 0x202e}, {0x2060, 0x2064},
{0x206a, 0x206f}, {0x0001d173, 0x0001d17a}, {0x000e0020, 0x000e007f}
``` [3](#0-2) 

Range `{0x2028, 0x202e}` correctly covers the "explicit" BiDi embedding/override formatting characters (U+202A LRE, U+202B RLE, U+202C PDF, U+202D LRO, U+202E RLO — exactly the first half of the range named in the Symfony advisory, "U+202A–U+202E"). However, the second half named in that advisory, "U+2066–U+2069" (the BiDi **isolate** characters LRI/RLI/FSI/PDI), falls in the gap between `{0x2060, 0x2064}` and `{0x206a, 0x206f}` — codepoints 0x2065 through 0x2069 are covered by **neither** range. This is a direct structural analog to the Symfony bug: a deny-list intended to neutralize the full BiDi-formatting attack surface is underinclusive for a documented sub-range of that same attack surface, and the omission sits exactly at the boundary between two otherwise-contiguous escaped ranges — a classic off-by-a-subrange gap, not an intentional design choice (the surrounding ranges show clear intent to escape "Invisible etc." (2060-2064) and "Shaping etc." (206a-206f), with the isolate marks apparently dropped from between them).

### Impact Explanation
Any client-controlled string field (via binary Protobuf or ProtoJSON parse, then re-serialized to JSON by this writer, or via direct ProtoJSON serialization of a message containing these characters) can smuggle U+2066/2067/2068/2069 unescaped into JSON output. Any downstream consumer that renders or logs the JSON string as-is (browser console/DevTools, log dashboards, terminal output, PDF/report generators, or any UI trusting `\uXXXX`-escaped JSON as "safe of exotic formatting characters") will render the BiDi isolate override in effect, enabling the same reverse-reading / homograph / visual-spoofing attacks (e.g., disguising a malicious filename, URL, or username as benign) that the Symfony advisory addresses. This is a Medium-severity integrity/spoofing issue (CWE-1007 Insufficient Visual Distinction of Homoglyphs, CWE-451 UI Misrepresentation) consistent with the analog report's severity, not a memory-safety or RCE issue.

### Likelihood Explanation
High likelihood of reachability: any application that accepts untrusted Protobuf/ProtoJSON input and re-emits it as JSON via the standard C++ `MessageToJsonString`/`JsonWriter` path is affected with no special configuration required — placing U+2066 (or 2067–2069) in any `string` field is sufficient, and no additional gating flag is needed to trigger the missing-escape behavior.

### Recommendation
Extend `kEscapedRanges` in `MustEscape()` (`src/google/protobuf/json/internal/writer.cc`) to close the gap, e.g. merge into a single contiguous range `{0x2028, 0x202e}` ∪ `{0x2060, 0x2069}` ∪ `{0x206a, 0x206f}` (or simply widen to `{0x2060, 0x206f}`) so U+2065–U+2069 are escaped along with the rest of the General Punctuation "invisible/format" block. Audit other language JSON writers (Java, Python/upb, C#, Rust) for the same or an equivalently complete list to ensure cross-language consistency, since this C++ file is the reference implementation for the escape policy comment ("originally present in the ESF parser").

### Proof of Concept
Given a proto message `message M { string s = 1; }`, set `s` to UTF-8 bytes for U+2067 (RLI): `E2 81 A7`. Serializing via `google::protobuf::util::MessageToJsonString(m, &json)` (which routes through `JsonWriter::WriteEscapedUtf8`) produces JSON containing the raw 3-byte UTF-8 sequence for U+2067 instead of `\u2067`, because `MustEscape(0x2067, ...)` falls through to the `default` case and `0x2067` matches none of the ranges in `kEscapedRanges` — confirmed by direct inspection of the range table at `src/google/protobuf/json/internal/writer.cc:252-267`, where `{0x2060, 0x2064}` ends at `0x2064` and `{0x206a, 0x206f}` begins at `0x206a`, leaving `0x2065`–`0x2069` unmatched. This was verified by static analysis of the range boundaries rather than by running the compiled writer; a Devin session with build/test access would be needed to compile a minimal C++ TU and assert on the produced JSON string.

### Citations

**File:** src/google/protobuf/json/internal/writer.cc (L233-251)
```text
    // These are not required by the JSON spec, but help
    // to prevent security bugs in JavaScript.
    //
    // These were originally present in the ESF parser, so they are kept for
    // legacy compatibility (and because escaping most of these is in good
    // taste, regardless).
    case '<':
    case '>':
    case 0xfeff:      // Zero width no-break space.
    case 0xfff9:      // Interlinear annotation anchor.
    case 0xfffa:      // Interlinear annotation separator.
    case 0xfffb:      // Interlinear annotation terminator.
    case 0x00ad:      // Soft-hyphen.
    case 0x06dd:      // Arabic end of ayah.
    case 0x070f:      // Syriac abbreviation mark.
    case 0x17b4:      // Khmer vowel inherent Aq.
    case 0x17b5:      // Khmer vowel inherent Aa.
    case 0x000e0001:  // Language tag.
      return true;
```

**File:** src/google/protobuf/json/internal/writer.cc (L252-267)
```text
    default:
      static constexpr std::pair<uint32_t, uint32_t> kEscapedRanges[] = {
          {0x0000, 0x001f},          // ASCII control.
          {0x007f, 0x009f},          // High ASCII bytes.
          {0x0600, 0x0603},          // Arabic signs.
          {0x200b, 0x200f},          // Zero width etc.
          {0x2028, 0x202e},          // Separators etc.
          {0x2060, 0x2064},          // Invisible etc.
          {0x206a, 0x206f},          // Shaping etc.
          {0x0001d173, 0x0001d17a},  // Music formatting.
          {0x000e0020, 0x000e007f},  // TAG symbols.
      };

      return absl::c_any_of(kEscapedRanges, [scalar](auto range) {
        return range.first <= scalar && scalar <= range.second;
      });
```

**File:** src/google/protobuf/json/internal/writer.cc (L284-304)
```text
void JsonWriter::WriteEscapedUtf8(absl::string_view str) {
  while (!str.empty()) {
    static constexpr absl::CharSet kUnsafeChars =
        ~absl::CharSet::AsciiPrintable() | absl::CharSet("\"\\<>");

    auto [safe_prefix, rest] = SplitAtFirst(str, kUnsafeChars);
    if (!safe_prefix.empty()) {
      Write(safe_prefix);
      str = rest;
      if (str.empty()) {
        break;
      }
    }

    auto scalar = ConsumeUtf8Scalar(str);
    absl::string_view custom_escape;

    if (!MustEscape(scalar.u32, custom_escape)) {
      Write(scalar.utf8);
      continue;
    }
```
