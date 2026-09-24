The strongest actual Protobuf analog to the "incorrect emoji splitting" bug in Canto's Bio contract is the byte-offset string truncation in `TextFormat::Printer::PrintFieldValue`.

### Title
Byte-offset truncation of string fields in `TextFormat::Printer` can split multi-byte UTF-8 sequences, producing corrupted/invalid text output - (File: `src/google/protobuf/text_format.cc`)

### Summary
When `TextFormat::Printer::SetTruncateStringFieldLongerThan` is enabled, string field values are truncated with a raw byte-offset `substr()` call that has no awareness of UTF-8 code-point (let alone grapheme-cluster) boundaries. If an attacker-controlled string field happens to have a multi-byte UTF-8 sequence straddling the configured byte threshold, the printer emits a string literal containing a truncated/incomplete UTF-8 sequence, exactly mirroring the Bio contract's flaw of splitting bio text at a fixed byte offset without considering multi-byte emoji sequences.

### Finding Description
The truncation option is documented as an intentional, application-opt-in feature: [1](#0-0) 

Its implementation performs a plain byte-count `substr()` on the field value with no UTF-8 boundary check: [2](#0-1) 

This is structurally identical to the external report's flaw: the Bio contract split `bioTextBytes` at fixed 40-byte offsets without checking whether the split point fell inside a multi-byte/multi-codepoint emoji sequence (e.g., regional-indicator/tag-sequence flags), corrupting the rendered text. Here, `value.substr(0, truncate_string_field_longer_than_)` performs the same kind of blind byte-offset cut on a `std::string` that holds attacker-supplied UTF-8 content (e.g., a `string` field populated via a public binary/ProtoJSON parse API and later stringified with `DebugString()`/`Utf8DebugString()`/`TextFormat::PrintToString()` using this Printer option). If the cut lands mid-sequence, the resulting `"<partial-bytes>...<truncated>..."` literal contains invalid UTF-8 bytes embedded in an otherwise well-formed text-format string field, which is a corrupted, unfixable representation of the original content (the true suffix bytes are discarded, exactly as in the Solidity bug where the split line "impossible to modify").

Notably, the codebase elsewhere (`HardenedPrintString`, `src/google/protobuf/text_format.cc:1877-1900`) explicitly guards against emitting broken UTF-8 by using `SkipPassthroughBytes`/`CEscape` to byte-escape any invalid trailing sequence — showing the project is aware of this exact class of hazard, but the truncation path added later does not reuse that boundary-aware logic.

### Impact Explanation
The impact is data-integrity/rendering corruption in the produced text-format output, not memory corruption: a consuming system that treats `TextFormat` string output as valid UTF-8 (or feeds it back through `TextFormat::Parse`) can receive a mid-sequence-truncated multi-byte character, causing downstream mis-decoding, mis-display, or parse failures — directly analogous to "incorrect display... impossible to modify... integration problems" from the source report. It is explicitly flagged as breaking "round-trip safe" behavior in the header comment, confirming the maintainers already know this path produces non-canonical/unsafe output; they simply didn't extend that awareness to UTF-8 well-formedness at the cut point.

### Likelihood Explanation
`TextFormat` is explicitly documented as unsuitable for untrusted-input wire use (`text_format.h` lines ~103-110), and `SetTruncateStringFieldLongerThan` is an opt-in application feature, not the default path for `ParseFrom*`. Reaching this requires: (1) an attacker able to control a `string` field's bytes (trivial via any parse API), and (2) the consuming application choosing to enable truncated debug/text printing on that message. This narrows likelihood to Low/Medium — it is a real, reachable but non-default, cosmetic/data-corruption issue rather than a memory-safety or wire-parsing vulnerability.

### Recommendation
When truncating, snap the cut point backward to the nearest valid UTF-8 code-point boundary (and ideally to a grapheme-cluster boundary for extended sequences like ZWJ emoji), mirroring the boundary-aware logic already used in `HardenedPrintString`/`SkipPassthroughBytes`, rather than performing a raw byte-count `substr()`.

### Proof of Concept
Construct a `TestAllTypes` message whose `optional_string` field is set to `N` valid ASCII bytes immediately followed by a 4-byte UTF-8 emoji (e.g., 😁 = `F0 9F 98 81`), where `N` equals `truncate_string_field_longer_than_`. Configure:
```cpp
TextFormat::Printer printer;
printer.SetTruncateStringFieldLongerThan(N);
std::string out;
printer.PrintToString(message, &out);
```
The resulting `optional_string: "...<N ascii bytes><partial-emoji-byte(s)>...<truncated>..."` contains an incomplete leading byte of the 4-byte sequence, which is not valid UTF-8 — analogous to the Solidity PoC's `strLines[1]` containing a fragment of the flag emoji.

### Citations

**File:** src/google/protobuf/text_format.h (L424-434)
```text
    // If non-zero, we truncate all string fields that are  longer than
    // this threshold.  This is useful when the proto message has very long
    // strings, e.g., dump of encoded image file.
    //
    // NOTE:  Setting a non-zero value breaks round-trip safe
    // property of TextFormat::Printer.  That is, from the printed message, we
    // cannot fully recover the original string field any more.
    void SetTruncateStringFieldLongerThan(
        const int64_t truncate_string_field_longer_than) {
      truncate_string_field_longer_than_ = truncate_string_field_longer_than;
    }
```

**File:** src/google/protobuf/text_format.cc (L2968-2991)
```text
    case FieldDescriptor::CPPTYPE_STRING: {
      std::string scratch;
      const std::string& value =
          field->is_repeated()
              ? reflection->GetRepeatedStringReference(message, field, index,
                                                       &scratch)
              : reflection->GetStringReference(message, field, &scratch);
      const std::string* value_to_print = &value;
      std::string truncated_value;
      if (truncate_string_field_longer_than_ > 0 &&
          static_cast<size_t>(truncate_string_field_longer_than_) <
              value.size()) {
        truncated_value = value.substr(0, truncate_string_field_longer_than_) +
                          "...<truncated>...";
        value_to_print = &truncated_value;
      }
      if (field->type() == FieldDescriptor::TYPE_STRING) {
        printer->PrintString(*value_to_print, generator);
      } else {
        ABSL_DCHECK_EQ(field->type(), FieldDescriptor::TYPE_BYTES);
        printer->PrintBytes(*value_to_print, generator);
      }
      break;
    }
```
