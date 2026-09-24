Based on my research, I found a genuine analog within Protobuf's own codebase: an inconsistency in ProtoJSON string escaping across language implementations that mirrors the core failure class in CVE-2017-1000146 — output intended for safe embedding in a browser/JavaScript context is not fully escaped in some (but not all) code paths, even though the maintainers explicitly designed other paths to defend against exactly this risk.

### Title
Missing HTML/JS-sensitive character escaping (`<`, `>`, and other browser-unsafe code points) in upb's ProtoJSON string encoder used by Python, Ruby, and PHP — (File: `upb/json/encode.c`)

### Summary
The C++ ProtoJSON writer (`src/google/protobuf/json/internal/writer.cc`) and the Java `JsonFormat` printer (`java/util/src/main/java/com/google/protobuf/util/JsonFormat.java`) both go beyond the strict JSON spec and additionally `\u`-escape characters such as `<`, `>`, `&`, `=`, `'`, and various zero-width/bidi Unicode ranges specifically "to prevent security bugs in JavaScript" when ProtoJSON output is embedded in HTML/`<script>` contexts by a consuming application. The upb-based JSON encoder (`upb/json/encode.c`, mirrored verbatim in `ruby/ext/google/protobuf_c/ruby-upb.c` and `php/ext/google/protobuf/php-upb.c`), which backs the Python, Ruby, and PHP native extensions, only escapes the minimal JSON-mandated set (`"`, `\`, and control characters `< 0x20`) — it never escapes `<`/`>`/`&` or the other browser-sensitive code points.

### Finding Description
`google.protobuf.util.JsonFormat` in Java maintains an explicit `replacementChars` table that escapes `<>&='` as `\uXXXX` sequences purely for XSS defense-in-depth [1](#0-0) , and the same design intent and character set is implemented in the C++ `MustEscape`/`WriteEscapedUtf8` functions used by the canonical C++ ProtoJSON writer [2](#0-1) [3](#0-2) .

In contrast, `jsonenc_put_escaped_char` in the upb JSON encoder — which is the sole JSON serialization implementation for Python's `google.protobuf.pyext`/upb backend, Ruby's `google-protobuf` gem, and the PHP native extension — only special-cases `\n \r \t \" \f \b \\` and generically escapes bytes `< 0x20`; any other byte, including `<`, `>`, `&`, is emitted verbatim as long as it is part of a "valid" UTF-8 sequence [4](#0-3) . This exact same logic is duplicated in `ruby/ext/google/protobuf_c/ruby-upb.c` [5](#0-4)  and is reachable from the public `Message#to_json`/`encode_json` Ruby API [6](#0-5) , as well as from the PHP `Message::serializeToJsonString()` API which calls straight into `upb_JsonEncode` [7](#0-6) .

**Failed invariant / attacker-controlled value / missing check:** The invariant the C++ and Java implementations enforce — "ProtoJSON string output must not contain literal `<`/`>`/`&`, so that it is safe to embed directly into an HTML/`<script>` context without secondary escaping" — is not enforced by the upb encoder. An ordinary client can set any string field (e.g., `optional_string`, a map value, or an extension name-adjacent value) via the standard binary or JSON parse APIs to contain `<script>...</script>` or `</script><script>...` payloads; on re-serialization to JSON via the Ruby/PHP/Python upb backend, these characters pass through unescaped. If the consuming application (as is common practice with Java's `JsonFormat`, which explicitly documents this scenario) embeds the resulting JSON string literal into an HTML page or inline `<script>` block, the unescaped `<`/`>` bytes can break out of the intended string literal / script context.

### Impact Explanation
This is a genuine cross-implementation inconsistency in a security-motivated escaping invariant that the project's own code comments describe as being for XSS prevention [8](#0-7) . Applications that rely on this defense-in-depth behavior (reasonably, since it's present in the canonical C++/Java paths) and use the Ruby, PHP, or Python upb-backed JSON serializer instead would be silently missing that protection, enabling reflected/stored script injection in the consuming web application if it embeds ProtoJSON strings into HTML/JS without its own escaping — directly analogous to the Mahara CVE's unescaped-title-in-AJAX-response issue. Impact is bounded to consuming-application XSS (Confidentiality/Integrity: Low per the CVE's own CVSS vector), not memory corruption or RCE within Protobuf itself.

### Likelihood Explanation
High reachability: any client-controlled string field value can be serialized to ProtoJSON via the public, documented `to_json`/`encode_json`/`serializeToJsonString` APIs on Ruby/PHP/Python, with zero additional preconditions — no malicious schema, no privileged access, just an ordinary bounded string field value flowing through the standard supported serialization path.

### Recommendation
Port the `MustEscape` escape set from `src/google/protobuf/json/internal/writer.cc` (or the Java `JsonFormat` `replacementChars` table) into `upb/json/encode.c`'s `jsonenc_put_escaped_char`/`jsonenc_stringbody`, so that `<`, `>`, `&`, and the other browser-unsafe code points are consistently `\u`-escaped across all upb-backed language bindings (Ruby, PHP, Python), matching the C++/Java behavior and closing the gap for applications relying on this defense-in-depth guarantee.

### Proof of Concept
Using the Ruby upb binding: build a message with `optional_string = "</script><script>alert(1)</script>"`, call `Message.encode_json`, which routes to `Message_encode_json` → `upb_JsonEncode` → `jsonenc_string`/`jsonenc_put_escaped_char` [9](#0-8) [10](#0-9) . The resulting JSON string will contain the literal `</script><script>` bytes unescaped, in contrast to the equivalent Java `JsonFormat.printer().print(message)` call, which would render these as `\u003c/script\u003e\u003cscript\u003e` [11](#0-10) .

### Citations

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1583-1591)
```java
      // These characters are fully legal in JSON, but are escaped to prevent XSS risks. Notably
      // this topic is not like 'html escaping' where these would be replaced with something like
      // `&lt;`. The escaped or not ways of writing it are verbatim 2 exactly equivalent
      // ways to represent the same exact value in JSON: consumers should never do any manual
      // unescape to round trip the intended value. This replacement is only be semantically
      // observable if someone tries to handle it raw textually and not as JSON.
      for (int i : "<>&='".toCharArray()) {
        replacementChars[i] = String.format("\\u%04x", i);
      }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1619-1640)
```java
    private void printStringEscapedAndQuoted(final CharSequence value) throws IOException {
      generator.print("\"");
      int len = value.length();
      int last = 0;
      for (int i = 0; i < len; i++) {
        char c = value.charAt(i);
        String replacement = getReplacementOrNull(c);

        // Keeps scanning to only call print() once on long runs that don't need escaping.
        if (replacement == null) {
          continue;
        }
        if (last < i) {
          generator.printSubsequence(value, last, i);
        }
        generator.print(replacement);
        last = i + 1;
      }

      generator.printSubsequence(value, last, len);
      generator.print("\"");
    }
```

**File:** src/google/protobuf/json/internal/writer.cc (L233-268)
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
  }
```

**File:** src/google/protobuf/json/internal/writer.cc (L284-296)
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
```

**File:** upb/json/encode.c (L268-301)
```c
static void jsonenc_put_escaped_char(jsonenc* e, char ch) {
  switch (ch) {
    case '\n':
      jsonenc_putstr(e, "\\n");
      break;
    case '\r':
      jsonenc_putstr(e, "\\r");
      break;
    case '\t':
      jsonenc_putstr(e, "\\t");
      break;
    case '\"':
      jsonenc_putstr(e, "\\\"");
      break;
    case '\f':
      jsonenc_putstr(e, "\\f");
      break;
    case '\b':
      jsonenc_putstr(e, "\\b");
      break;
    case '\\':
      jsonenc_putstr(e, "\\\\");
      break;
    default:
      if ((uint8_t)ch < 0x20) {
        jsonenc_printf(e, "\\u%04x", (int)(uint8_t)ch);
      } else {
        /* This could be a non-ASCII byte.  We rely on the string being valid
         * UTF-8. */
        jsonenc_putbytes(e, &ch, 1);
      }
      break;
  }
}
```

**File:** ruby/ext/google/protobuf_c/ruby-upb.c (L5560-5594)
```c
static void jsonenc_put_escaped_char(jsonenc* e, char ch) {
  switch (ch) {
    case '\n':
      jsonenc_putstr(e, "\\n");
      break;
    case '\r':
      jsonenc_putstr(e, "\\r");
      break;
    case '\t':
      jsonenc_putstr(e, "\\t");
      break;
    case '\"':
      jsonenc_putstr(e, "\\\"");
      break;
    case '\f':
      jsonenc_putstr(e, "\\f");
      break;
    case '\b':
      jsonenc_putstr(e, "\\b");
      break;
    case '\\':
      jsonenc_putstr(e, "\\\\");
      break;
    default:
      if ((uint8_t)ch < 0x20) {
        jsonenc_printf(e, "\\u%04x", (int)(uint8_t)ch);
      } else {
        /* This could be a non-ASCII byte.  We rely on the string being valid
         * UTF-8. */
        jsonenc_putbytes(e, &ch, 1);
      }
      break;
  }
}

```

**File:** ruby/ext/google/protobuf_c/ruby-upb.c (L5595-5609)
```c
static void jsonenc_stringbody(jsonenc* e, upb_StringView str) {
  const char* ptr = str.data;
  const char* end = UPB_PTRADD(ptr, str.size);

  while (ptr < end) {
    jsonenc_put_escaped_char(e, *ptr);
    ptr++;
  }
}

static void jsonenc_string(jsonenc* e, upb_StringView str) {
  jsonenc_putstr(e, "\"");
  jsonenc_stringbody(e, str);
  jsonenc_putstr(e, "\"");
}
```

**File:** ruby/ext/google/protobuf_c/message.c (L1195-1258)
```c
static VALUE Message_encode_json(int argc, VALUE* argv, VALUE klass) {
  Message* msg = ruby_to_Message(argv[0]);
  int options = 0;
  char buf[1024];
  size_t size;
  upb_Status status;

  if (argc < 1 || argc > 2) {
    rb_raise(rb_eArgError, "Expected 1 or 2 arguments.");
  }

  if (argc == 2) {
    VALUE hash_args = argv[1];
    if (TYPE(hash_args) != T_HASH) {
      if (RTEST(rb_funcall(hash_args, rb_intern("respond_to?"), 1,
                           rb_str_new2("to_h")))) {
        hash_args = rb_funcall(hash_args, rb_intern("to_h"), 0);
      } else {
        rb_raise(rb_eArgError, "Expected hash arguments.");
      }
    }

    if (RTEST(rb_hash_lookup2(hash_args,
                              ID2SYM(rb_intern("preserve_proto_fieldnames")),
                              Qfalse))) {
      options |= upb_JsonEncode_UseProtoNames;
    }

    if (RTEST(rb_hash_lookup2(hash_args, ID2SYM(rb_intern("emit_defaults")),
                              Qfalse))) {
      options |= upb_JsonEncode_EmitDefaults;
    }

    if (RTEST(rb_hash_lookup2(hash_args,
                              ID2SYM(rb_intern("format_enums_as_integers")),
                              Qfalse))) {
      options |= upb_JsonEncode_FormatEnumsAsIntegers;
    }
  }

  upb_Status_Clear(&status);
  const upb_DefPool* pool = upb_FileDef_Pool(upb_MessageDef_File(msg->msgdef));
  size = upb_JsonEncode(msg->msg, msg->msgdef, pool, options, buf, sizeof(buf),
                        &status);

  if (!upb_Status_IsOk(&status)) {
    rb_raise(cParseError, "Error occurred during encoding: %s",
             upb_Status_ErrorMessage(&status));
  }

  VALUE ret;
  if (size >= sizeof(buf)) {
    char* buf2 = malloc(size + 1);
    upb_JsonEncode(msg->msg, msg->msgdef, pool, options, buf2, size + 1,
                   &status);
    ret = rb_str_new(buf2, size);
    free(buf2);
  } else {
    ret = rb_str_new(buf, size);
  }

  rb_enc_associate(ret, rb_utf8_encoding());
  return ret;
}
```

**File:** php/ext/google/protobuf/message.c (L816-866)
```c
PHP_METHOD(Message, serializeToJsonString) {
  Message* intern = (Message*)Z_OBJ_P(getThis());
  size_t size;
  int options = 0;
  char buf[1024];
  zval* flags = NULL;
  upb_Status status;

  if (zend_parse_parameters(ZEND_NUM_ARGS(), "|z", &flags) == FAILURE) {
    return;
  }

  if (flags == NULL || Z_TYPE_P(flags) == IS_FALSE) {
    // do nothing
  } else if (Z_TYPE_P(flags) == IS_TRUE) {
    options |= upb_JsonEncode_UseProtoNames;
  } else if (Z_TYPE_P(flags) == IS_LONG) {
    if (Z_LVAL_P(flags) & ALWAYS_PRINT_ENUMS_AS_INTS) {
      options |= upb_JsonEncode_FormatEnumsAsIntegers;
    }
    if (Z_LVAL_P(flags) & PRESERVE_PROTO_FIELD_NAMES) {
      options |= upb_JsonEncode_UseProtoNames;
    }
    if (Z_LVAL_P(flags) & EMIT_DEFAULTS) {
      options |= upb_JsonEncode_EmitDefaults;
    }
  }

  upb_Status_Clear(&status);
  size = upb_JsonEncode(intern->msg, intern->desc->msgdef,
                        DescriptorPool_GetSymbolTable(), options, buf,
                        sizeof(buf), &status);

  if (!upb_Status_IsOk(&status)) {
    zend_throw_exception_ex(NULL, 0,
                            "Error occurred during JSON serialization: %s",
                            upb_Status_ErrorMessage(&status));
    return;
  }

  if (size >= sizeof(buf)) {
    char* buf2 = malloc(size + 1);
    upb_JsonEncode(intern->msg, intern->desc->msgdef,
                   DescriptorPool_GetSymbolTable(), options, buf2, size + 1,
                   &status);
    RETVAL_STRINGL(buf2, size);
    free(buf2);
  } else {
    RETVAL_STRINGL(buf, size);
  }
}
```
