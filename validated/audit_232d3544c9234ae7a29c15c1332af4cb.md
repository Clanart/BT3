## Finding: FieldMask path→JSON conversion utilities do not reject delimiter characters, allowing path‑list injection

### Title
FieldMask string injection via unchecked comma characters in `FieldMaskUtil::ToJsonString` - (File: `src/google/protobuf/util/field_mask_util.cc`)

### Summary
Tornado's flaw was that `RequestHandler.set_cookie`'s `domain`/`path`/`samesite` arguments were written verbatim into a delimiter-separated header without checking for characters (`;`, CRLF) that have syntactic meaning in that format, letting an attacker inject extra cookie attributes. The closest Protobuf analog is `FieldMaskUtil::ToJsonString` (and its Java/Python siblings), which serializes each `FieldMask.paths` string into a single comma‑delimited output string but never rejects the comma delimiter itself inside a path value, so one attacker-controlled path element can be rendered as two (or more) path elements in the emitted list.

### Finding Description
`google.protobuf.FieldMask.paths` is an ordinary `repeated string` field, so a value parsed from untrusted binary Protobuf can contain any UTF-8 bytes, including `,`. [1](#0-0) 

`FieldMaskUtil::ToJsonString` converts each path with `SnakeCaseToCamelCase` and joins the results with `,` to build the ProtoJSON string representation of the mask: [2](#0-1) 

`SnakeCaseToCamelCase` only rejects uppercase letters and enforces the "lowercase after underscore" rule; every other character — including `,`, quotes, or control characters — is passed straight through to the output via `output->push_back(input_char)`: [3](#0-2) 

The equivalent Java helper `lowerUnderscoreToLowerCamel`/`toJsonString` has the identical gap — it only special-cases `_` and never rejects `,`: [4](#0-3) 
The Python `_SnakeCaseToCamelCase`/`ToJsonString` implementation is the same: it raises only on uppercase letters or bad underscore placement, never on `,`: [5](#0-4) 

This is a materially weaker invariant than the one enforced by the actual ProtoJSON serializer used by `MessageToJsonString`/`util::JsonStringPrinter`, which explicitly validates every character of a FieldMask path and returns `absl::InvalidArgumentError("unexpected character in FieldMask")` for anything other than a digit, lowercase letter, `.` or properly-placed `_`: [6](#0-5) 
That hardened path is also exercised by the `upb`/PHP/Ruby encoders, which similarly reject or escape out-of-band characters rather than letting them pass through raw. [7](#0-6) 

So `FieldMaskUtil::ToJsonString` (C++/Java/Python) is a second, independently-implemented FieldMask→string converter that omits the character-validation invariant the "real" JSON printer enforces.

### Impact Explanation
Consuming-application exposure assumption: many gRPC/REST gateway implementations following the Google API Improvement Proposals accept a binary/ProtoJSON `FieldMask` from a client, and then use `FieldMaskUtil::ToJsonString` (or the Java/Python equivalents) to build the comma-separated `update_mask` string that is forwarded to another layer (e.g., a REST `?update_mask=...` query parameter, an audit log, or a downstream RPC argument) which itself splits on `,` to obtain the effective list of fields. Because a single `FieldMask.paths` entry supplied by the client is not restricted from containing a literal `,`, one path value such as `"update_time,secret_field"` is rendered by `ToJsonString` as `"updateTime,secretField"` — i.e., a single attacker-supplied string is expanded into two syntactically distinct field paths in the emitted mask, without any comma-splitting validation having been applied to the original path. If the original mask was validated as a whole (e.g., only checking exact path strings) before this conversion, the injected extra path bypasses that check when a downstream consumer re-splits the comma-joined string, potentially causing an unintended field to be treated as part of the update mask (an integrity/authorization-relevant miswrite, not a crash). This matches the CWE-159 "special element not properly neutralized" class from the Tornado report.

### Likelihood Explanation
Reaching this requires only that an ordinary client supply a `FieldMask.paths` string containing a comma via a normal binary/ProtoJSON parse of a message embedding `google.protobuf.FieldMask`, and that the trusted application call `FieldMaskUtil::ToJsonString`/`toJsonString`/`ToJsonString` to re-serialize it for a downstream consumer that treats the comma as a field separator. No malformed input, size limits, or privileged access are needed — a single low-effort crafted string field is sufficient. However, the impact is entirely contingent on how the specific consuming application interprets the resulting comma-joined string, which is outside Protobuf's control; there is no reachable memory-safety or Protobuf-internal integrity impact from this call alone.

### Recommendation
Harden `FieldMaskUtil::ToJsonString`/`SnakeCaseToCamelCase` (and the Java/Python equivalents) to reject any character that is not `[a-z0-9_.]` (mirroring the validation already performed by `json/internal/unparser.cc`'s `WriteFieldMask`), returning failure/throwing instead of silently emitting the raw byte. This closes the gap between the "legacy" FieldMask utility converters and the properly-hardened ProtoJSON `WriteFieldMask` path.

### Proof of Concept
```cpp
google::protobuf::FieldMask mask;
mask.add_paths("update_time,secret_field");  // single attacker-controlled path value

std::string json;
bool ok = google::protobuf::util::FieldMaskUtil::ToJsonString(mask, &json);
// ok == true
// json == "updateTime,secretField"
// -> a single FieldMask.paths entry has become two comma-separated
//    path tokens in the emitted string, without any comma-aware
//    validation ever being applied to the original path value.
```
Compare with the properly-hardened JSON printer, which rejects the same input: [8](#0-7)  — a `,` inside a path causes `absl::InvalidArgumentError("unexpected character in FieldMask")` there, showing `FieldMaskUtil::ToJsonString` is missing the same check.

### Citations

**File:** src/google/protobuf/field_mask.proto (L181-206)
```text
// separated by a comma. Fields name in each path are converted
// to/from lower-camel naming conventions.
//
// As an example, consider the following message declarations:
//
//     message Profile {
//       User user = 1;
//       Photo photo = 2;
//     }
//     message User {
//       string display_name = 1;
//       string address = 2;
//     }
//
// In proto a field mask for `Profile` may look as such:
//
//     mask {
//       paths: "user.display_name"
//       paths: "photo"
//     }
//
// In JSON, the same mask is represented as below:
//
//     {
//       mask: "user.displayName,photo"
//     }
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

**File:** src/google/protobuf/util/field_mask_util.cc (L100-114)
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
```

**File:** java/util/src/main/java/com/google/protobuf/util/FieldMaskUtil.java (L136-180)
```java
  /** Converts a lower_underscore to lowerCamelCase style. */
  private static String lowerUnderscoreToLowerCamel(String str) {
    StringBuilder sb = new StringBuilder();
    boolean capitalizeNext = false;
    for (int i = 0; i < str.length(); i++) {
      char c = str.charAt(i);
      if (c == '_') {
        capitalizeNext = true;
      } else if (capitalizeNext) {
        sb.append(Character.toUpperCase(c));
        capitalizeNext = false;
      } else {
        sb.append(Character.toLowerCase(c));
      }
    }
    return sb.toString();
  }

  /** Converts a lowerCamelCase string to lower_underscore style. */
  private static String lowerCamelToLowerUnderscore(String str) {
    StringBuilder sb = new StringBuilder();
    for (int i = 0; i < str.length(); i++) {
      char c = str.charAt(i);
      if (c >= 'A' && c <= 'Z') {
        sb.append('_');
      }
      sb.append(Character.toLowerCase(c));
    }
    return sb.toString();
  }

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
```

**File:** python/google/protobuf/internal/field_mask.py (L17-158)
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

  def IsValidForDescriptor(self, message_descriptor):
    """Checks whether the FieldMask is valid for Message Descriptor."""
    for path in self.paths:
      if not _IsValidPath(message_descriptor, path):
        return False
    return True

  def AllFieldsFromDescriptor(self, message_descriptor):
    """Gets all direct fields of Message Descriptor to FieldMask."""
    self.Clear()
    for field in message_descriptor.fields:
      self.paths.append(field.name)

  def CanonicalFormFromMask(self, mask):
    """Converts a FieldMask to the canonical form.

    Removes paths that are covered by another path. For example,
    "foo.bar" is covered by "foo" and will be removed if "foo"
    is also in the FieldMask. Then sorts all paths in alphabetical order.

    Args:
      mask: The original FieldMask to be converted.
    """
    tree = _FieldMaskTree(mask)
    tree.ToFieldMask(self)

  def Union(self, mask1, mask2):
    """Merges mask1 and mask2 into this FieldMask."""
    _CheckFieldMaskMessage(mask1)
    _CheckFieldMaskMessage(mask2)
    tree = _FieldMaskTree(mask1)
    tree.MergeFromFieldMask(mask2)
    tree.ToFieldMask(self)

  def Intersect(self, mask1, mask2):
    """Intersects mask1 and mask2 into this FieldMask."""
    _CheckFieldMaskMessage(mask1)
    _CheckFieldMaskMessage(mask2)
    tree = _FieldMaskTree(mask1)
    intersection = _FieldMaskTree()
    for path in mask2.paths:
      tree.IntersectPath(path, intersection)
    intersection.ToFieldMask(self)

  def MergeMessage(
      self,
      source,
      destination,
      replace_message_field=False,
      replace_repeated_field=False,
  ):
    """Merges fields specified in FieldMask from source to destination.

    Args:
      source: Source message.
      destination: The destination message to be merged into.
      replace_message_field: Replace message field if True. Merge message field
        if False.
      replace_repeated_field: Replace repeated field if True. Append elements of
        repeated field if False.
    """
    tree = _FieldMaskTree(self)
    tree.MergeMessage(
        source, destination, replace_message_field, replace_repeated_field
    )


def _IsValidPath(message_descriptor, path):
  """Checks whether the path is valid for Message Descriptor."""
  parts = path.split('.')
  last = parts.pop()
  for name in parts:
    field = message_descriptor.fields_by_name.get(name)
    if (
        field is None
        or field.is_repeated
        or field.type != FieldDescriptor.TYPE_MESSAGE
    ):
      return False
    message_descriptor = field.message_type
  return last in message_descriptor.fields_by_name


def _CheckFieldMaskMessage(message):
  """Raises ValueError if message is not a FieldMask."""
  message_descriptor = message.DESCRIPTOR
  if (
      message_descriptor.name != 'FieldMask'
      or message_descriptor.file.name != 'google/protobuf/field_mask.proto'
  ):
    raise ValueError(
        'Message {0} is not a FieldMask.'.format(message_descriptor.full_name)
    )


def _SnakeCaseToCamelCase(path_name):
  """Converts a path name from snake_case to camelCase."""
  result = []
  after_underscore = False
  for c in path_name:
    if c.isupper():
      raise ValueError(
          'Fail to print FieldMask to Json string: Path name '
          '{0} must not contain uppercase letters.'.format(path_name)
      )
    if after_underscore:
      if c.islower():
        result.append(c.upper())
        after_underscore = False
      else:
        raise ValueError(
            'Fail to print FieldMask to Json string: The '
            'character after a "_" must be a lowercase letter '
            'in path name {0}.'.format(path_name)
        )
    elif c == '_':
      after_underscore = True
    else:
      result += c

  if after_underscore:
    raise ValueError(
        'Fail to print FieldMask to Json string: Trailing "_" '
        'in path name {0}.'.format(path_name)
    )
  return ''.join(result)
```

**File:** src/google/protobuf/json/internal/unparser.cc (L738-767)
```text
  for (size_t i = 0; i < paths; ++i) {
    writer.WriteComma(first);
    auto path = Traits::GetString(paths_field, writer.ScratchBuf(), msg, i);
    RETURN_IF_ERROR(path.status());
    bool saw_under = false;
    for (char c : *path) {
      if (absl::ascii_islower(c) && saw_under) {
        writer.Write(absl::ascii_toupper(c));
      } else if (absl::ascii_isdigit(c) || absl::ascii_islower(c) || c == '.') {
        writer.Write(c);
      } else if (c == '_' &&
                 (!saw_under ||
                  writer.options().allow_legacy_nonconformant_behavior)) {
        saw_under = true;
        continue;
      } else if (!writer.options().allow_legacy_nonconformant_behavior) {
        return absl::InvalidArgumentError("unexpected character in FieldMask");
      } else {
        if (saw_under) {
          writer.Write('_');
        }
        writer.Write(c);
      }
      saw_under = false;
    }
  }
  writer.Write('"');

  return absl::OkStatus();
}
```

**File:** upb/json/encode.c (L461-478)
```c
static void jsonenc_fieldmask(jsonenc* e, const upb_Message* msg,
                              const upb_MessageDef* m) {
  const upb_FieldDef* paths_f = upb_MessageDef_FindFieldByNumber(m, 1);
  const upb_Array* paths = upb_Message_GetFieldByDef(msg, paths_f).array_val;
  bool first = true;
  size_t i, n = 0;

  if (paths) n = upb_Array_Size(paths);

  jsonenc_putstr(e, "\"");

  for (i = 0; i < n; i++) {
    jsonenc_putsep(e, ",", &first);
    jsonenc_fieldpath(e, upb_Array_Get(paths, i).str_val);
  }

  jsonenc_putstr(e, "\"");
}
```
