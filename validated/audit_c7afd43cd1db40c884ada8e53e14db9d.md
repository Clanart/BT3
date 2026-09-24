## Summary of Findings

The Sherlock report's failed invariant is: **concatenating multiple attacker-controlled dynamic-length values with `abi.encodePacked` (no delimiter/length-prefix) allows different splits of the data to produce byte-identical output**, which is then hashed and used for a security decision (`signedOnly`), so the check can be bypassed by re-splitting the same bytes differently.

The closest real analog in this Protobuf codebase is in `google.protobuf.FieldMask`'s string/JSON conversion utilities. `FieldMask.paths` is a `repeated string` with **no character restrictions at the wire level** — a client can legally send (via the standard binary `Parse`/`MergeFrom` API) a path value containing a literal `,` character. Every language's `FieldMask`⇄string conversion (`ToJsonString`/`FromJsonString`, `ToString`/`FromString`) joins/splits multiple dynamic-length path strings using an **unescaped comma delimiter**, exactly the same "concatenate dynamic values with no delimiter-escaping" pattern that broke `abi.encodePacked`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

None of these serialization-direction functions (`ToJsonString`/`ToString`/PHP `formatFieldMask`) validate that a path element is free of the `,` delimiter before joining; only casing rules are checked (`SnakeCaseToCamelCase`/`IsPathValid`/`IsPathValid`). This means one `paths` entry containing an embedded comma is indistinguishable, once serialized, from two separate path entries — the exact ambiguity class described in the Sherlock report.

### Title
FieldMask path-to-string serialization allows delimiter-injection collisions between distinct path sets - (File: `src/google/protobuf/util/field_mask_util.cc`)

### Summary
`google.protobuf.FieldMask.paths` is an unrestricted `repeated string`. Binary Protobuf parsing places no character constraints on these strings, so a client can legally submit a `FieldMask` whose single path element contains a literal comma (e.g. `"b,c"`). When trusted application code later converts that mask to its canonical/JSON string form via `FieldMaskUtil::ToJsonString`/`ToString` (C++), `FieldMaskUtil.toJsonString` (Java), `FieldMask.ToJsonString` (Python), `FieldMask.ToJson`/`ToDiagnosticString` (C#), or `GPBUtil::formatFieldMask` (PHP) — all of which join paths with an un-escaped `,` — the resulting string is ambiguous: `["a", "b,c"]` serializes to `"a,b,c"`, byte-identical to what `["a","b","c"]` would produce. Any downstream consumer that re-parses this string with the corresponding `FromJsonString`/`FromString` recovers `["a","b","c"]` — a different, larger set of paths than the producer intended.

### Finding Description
This is structurally identical to the reported `abi.encodePacked` bug: two or more attacker-influenced dynamic-length values (`paths[i]`) are concatenated with a fixed separator but without escaping/length-prefixing the individual elements, so a single element containing the separator character collides with a boundary between two elements.
- Parse surface: any public binary `Parse`/`MergeFrom` on a message embedding a `google.protobuf.FieldMask` field accepts arbitrary bytes (including `,`) inside a `paths` string — this is standard, valid Protobuf and requires no privileged access.
- Missing check: `ToJsonString`/`ToString`/`formatFieldMask` never reject or escape a `,` inside an individual path before joining. Compare `SnakeCaseToCamelCase` (C++) [8](#0-7)  which only checks case rules — a comma passes straight through into the joined string.
- Consuming-application exposure assumption: applications that follow Google's own AIP-134 update-mask pattern often build/forward a `FieldMask` as a canonical comma-joined string (for logging, for cross-service propagation, or for re-validation by a second component using `FromJsonString`/`FromString`). If a second, trusted component re-parses that joined string as the source of truth for which fields are permitted to change, the attacker's single crafted path (`"b,c"`) is silently expanded into two independent paths (`"b"`, `"c"`) by that second parse — i.e., the reconstructed authorization set differs from what the first component computed/validated.
- This is *not* blocked by `FieldMaskUtil::IsValidPath`/`GetFieldDescriptors` unless the application explicitly calls that separate descriptor-validation API before serializing; many callers use `ToJsonString`/`ToString` directly without it.

### Impact Explanation
If a consuming service relies on the joined FieldMask string as an intermediate, re-parsable representation of "exactly which fields are authorized" (a common Google API pattern for `update_mask`/`read_mask`), the ambiguity lets a client’s intended single path silently split into multiple paths (or, in principle, adjacent paths to merge) after a serialize/reparse round trip. This is an integrity/authorization-adjacent issue: the set of paths acted upon downstream can diverge from what was validated upstream. It mirrors the Sherlock finding's classification: a real but narrow logic flaw dependent on how consuming code composes the library's serialize/parse primitives, not a memory-safety bug.

### Likelihood Explanation
Likelihood is Medium: the character sequence needed (`,` inside a path) is trivially producible in binary wire format, requires no schema or plugin tampering, and is reachable through the standard, public, binary `Parse` API. However, exploitation requires a specific application pattern (round-tripping a `FieldMask` through the string form across two independently-trusting components), which is not universal — same caveat as the original Sherlock report ("fails to show an exploit pattern" in the general case, downgraded but preserved as Medium).

### Recommendation
Add validation/escaping in `FieldMaskUtil::ToJsonString`/`ToString` (and equivalents in Java/Python/C#/PHP/upb) to reject or percent-encode any path segment containing the `,` delimiter before joining, matching the fix pattern for the original report (never build a compound value by naive concatenation/joining of untrusted dynamic values without an unambiguous encoding). Alternatively, mandate that `FieldMaskUtil::IsValidFieldMask`/`GetFieldDescriptors` against the target message descriptor be run before any serialize-to-string call, since a genuine schema field name can never contain a comma.

### Proof of Concept
Using the C++ utility (equivalent behavior demonstrated is portable to Java/Python/C#/PHP shown above):
```cpp
google::protobuf::FieldMask mask;
mask.add_paths("a");
mask.add_paths("b,c");   // single path, produced e.g. via binary Parse of a message with paths=["a","b,c"]

std::string json;
FieldMaskUtil::ToJsonString(mask, &json);
// json == "a,b,c"  -- ambiguous with a 3-path mask

FieldMask reparsed;
FieldMaskUtil::FromJsonString(json, &reparsed);
// reparsed.paths() == ["a", "b", "c"]  (3 entries)
// original mask.paths() == ["a", "b,c"] (2 entries)
```
The two masks are semantically different (2 vs. 3 target fields), yet produce the identical intermediate string `"a,b,c"`, demonstrating the same delimiter-collision class as `abi.encodePacked("a","bc") == abi.encodePacked("ab","c")` in the source report. [2](#0-1)

### Citations

**File:** src/google/protobuf/util/field_mask_util.cc (L39-50)
```text
std::string FieldMaskUtil::ToString(const FieldMask& mask) {
  return absl::StrJoin(mask.paths(), ",");
}

void FieldMaskUtil::FromString(absl::string_view str, FieldMask* out) {
  out->Clear();
  std::vector<absl::string_view> paths = absl::StrSplit(str, ',');
  for (absl::string_view path : paths) {
    if (path.empty()) continue;
    out->add_paths(path);
  }
}
```

**File:** src/google/protobuf/util/field_mask_util.cc (L52-80)
```text
bool FieldMaskUtil::SnakeCaseToCamelCase(absl::string_view input,
                                         std::string* output) {
  output->clear();
  bool after_underscore = false;
  for (char input_char : input) {
    if (input_char >= 'A' && input_char <= 'Z') {
      // The field name must not contain uppercase letters.
      return false;
    }
    if (after_underscore) {
      if (input_char >= 'a' && input_char <= 'z') {
        output->push_back(input_char + 'A' - 'a');
        after_underscore = false;
      } else {
        // The character after a "_" must be a lowercase letter.
        return false;
      }
    } else if (input_char == '_') {
      after_underscore = true;
    } else {
      output->push_back(input_char);
    }
  }
  if (after_underscore) {
    // Trailing "_".
    return false;
  }
  return true;
}
```

**File:** src/google/protobuf/util/field_mask_util.cc (L100-128)
```text
bool FieldMaskUtil::ToJsonString(const FieldMask& mask, std::string* out) {
  out->clear();
  for (int i = 0; i < mask.paths_size(); ++i) {
    absl::string_view path = mask.paths(i);
    std::string camelcase_path;
    if (!SnakeCaseToCamelCase(path, &camelcase_path)) {
      return false;
    }
    if (i > 0) {
      out->push_back(',');
    }
    out->append(camelcase_path);
  }
  return true;
}

bool FieldMaskUtil::FromJsonString(absl::string_view str, FieldMask* out) {
  out->Clear();
  std::vector<absl::string_view> paths = absl::StrSplit(str, ',');
  for (absl::string_view path : paths) {
    if (path.empty()) continue;
    std::string snakecase_path;
    if (!CamelCaseToSnakeCase(path, &snakecase_path)) {
      return false;
    }
    out->add_paths(snakecase_path);
  }
  return true;
}
```

**File:** java/util/src/main/java/com/google/protobuf/util/FieldMaskUtil.java (L167-197)
```java
  /**
   * Converts a field mask to a ProtoJSON string, that is converting from snake case to camel case
   * and joining all paths into one string with commas.
   */
  public static String toJsonString(FieldMask fieldMask) {
    List<String> paths = new ArrayList<String>(fieldMask.getPathsCount());
    for (String path : fieldMask.getPathsList()) {
      if (path.isEmpty()) {
        continue;
      }
      paths.add(lowerUnderscoreToLowerCamel(path));
    }
    return String.join(FIELD_PATH_SEPARATOR, paths);
  }

  /**
   * Converts a field mask from a ProtoJSON string, that is splitting the paths along commas and
   * converting from camel case to snake case.
   */
  @SuppressWarnings("StringSplitter")
  public static FieldMask fromJsonString(String value) {
    String[] paths = value.split(FIELD_PATH_SEPARATOR);
    FieldMask.Builder builder = FieldMask.newBuilder();
    for (String path : paths) {
      if (path.isEmpty()) {
        continue;
      }
      builder.addPaths(lowerCamelToLowerUnderscore(path));
    }
    return builder.build();
  }
```

**File:** python/google/protobuf/internal/field_mask.py (L17-31)
```python
  def ToJsonString(self):
    """Converts FieldMask to string according to ProtoJSON spec."""
    camelcase_paths = []
    for path in self.paths:
      camelcase_paths.append(_SnakeCaseToCamelCase(path))
    return ','.join(camelcase_paths)

  def FromJsonString(self, value):
    """Converts string to FieldMask according to ProtoJSON spec."""
    if not isinstance(value, str):
      raise ValueError('FieldMask JSON value not a string: {!r}'.format(value))
    self.Clear()
    if value:
      for path in value.split(','):
        self.paths.append(_CamelCaseToSnakeCase(path))
```

**File:** csharp/src/Google.Protobuf/WellKnownTypes/FieldMaskPartial.cs (L37-61)
```csharp
        internal static string ToJson(IList<string> paths, bool diagnosticOnly)
        {
            var firstInvalid = paths.FirstOrDefault(p => !IsPathValid(p));
            if (firstInvalid == null)
            {
                var writer = new StringWriter();
                JsonFormatter.WriteString(writer, string.Join(",", paths.Select(JsonFormatter.ToJsonName)));
                return writer.ToString();
            }
            else
            {
                if (diagnosticOnly)
                {
                    var writer = new StringWriter();
                    writer.Write("{ \"@warning\": \"Invalid FieldMask\", \"paths\": ");
                    JsonFormatter.Default.WriteList(writer, (IList)paths);
                    writer.Write(" }");
                    return writer.ToString();
                }
                else
                {
                    throw new InvalidOperationException($"Invalid field mask to be converted to JSON: {firstInvalid}");
                }
            }
        }
```

**File:** csharp/src/Google.Protobuf/WellKnownTypes/FieldMaskPartial.cs (L159-178)
```csharp
        private static bool IsPathValid(string input)
        {
            for (int i = 0; i < input.Length; i++)
            {
                char c = input[i];
                if (c >= 'A' && c <= 'Z')
                {
                    return false;
                }
                if (c == '_' && i < input.Length - 1)
                {
                    char next = input[i + 1];
                    if (next < 'a' || next > 'z')
                    {
                        return false;
                    }
                }
            }
            return true;
        }
```

**File:** php/src/Google/Protobuf/Internal/GPBUtil.php (L574-634)
```php
    public static function parseFieldMask($paths_string)
    {
        $field_mask = new FieldMask();
        if (strlen($paths_string) === 0) {
            return $field_mask;
        }
        $path_strings = explode(",", $paths_string);
        $paths = $field_mask->getPaths();
        foreach($path_strings as &$path_string) {
            $field_strings = explode(".", $path_string);
            foreach($field_strings as &$field_string) {
                $field_string = camel2underscore($field_string);
            }
            $path_string = implode(".", $field_strings);
            $paths[] = $path_string;
        }
        return $field_mask;
    }

    public static function formatFieldMask($field_mask)
    {
        $converted_paths = [];
        foreach($field_mask->getPaths() as $path) {
            $fields = explode('.', $path);
            $converted_path = [];
            foreach ($fields as $field) {
                if (preg_match('/[A-Z]/', $field)) {
                    trigger_error(
                        "Field mask element may not have upper-case letter. " .
                        "This will fail in a future version of Protobuf.",
                        E_USER_WARNING
                    );
                }
                if (preg_match('/_([^a-z]|$)/', $field)) {
                    trigger_error(
                        "Underscore in FieldMask path must be followed by a " .
                        "lowercase letter to successfully round trip through JSON format. " .
                        "See https://github.com/protocolbuffers/protobuf/issues/25786. " .
                        "This will fail in a future version of Protobuf.",
                        E_USER_WARNING
                    );
                }
                $segments = explode('_', $field);
                $start = true;
                $converted_segments = "";
                foreach($segments as $segment) {
                  if (!$start) {
                    $converted = ucfirst($segment);
                  } else {
                    $converted = $segment;
                    $start = false;
                  }
                  $converted_segments .= $converted;
                }
                $converted_path []= $converted_segments;
            }
            $converted_path = implode(".", $converted_path);
            $converted_paths []= $converted_path;
        }
        return implode(",", $converted_paths);
    }
```
