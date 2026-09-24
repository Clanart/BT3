Confirmed: the upb-based JSON encoder (`upb/json/encode.c`, and its vendored copies in `php/ext/google/protobuf/php-upb.c` and `ruby/ext/google/protobuf_c/ruby-upb.c`) escapes only the RFC-8259-mandated characters (`"`, `\`, control chars) in `jsonenc_put_escaped_char`, and explicitly passes through `<`, `>`, `&`, `=`, and other JS-unsafe characters unescaped via the `default` branch that just calls `jsonenc_putbytes`. This is exposed through the public `upb_JsonEncode` API, which PHP's `Message::serializeToJsonString`/`jsonSerialize`, Ruby's `Message#to_json`, and the Lua `upb.json_encode` bindings all call directly. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Missing HTML/JS-unsafe character escaping in upb ProtoJSON string encoder (PHP/Ruby/upb C/Lua) - [File: upb/json/encode.c]

### Summary
Kibana's flaw was that attacker-controlled document field text (an HTML snippet) was written into the page without contextual escaping when the Discover app highlighted it, letting the browser interpret it as markup. The transferable invariant is: *when serializer output may be embedded in an HTML/JavaScript context, characters like `<`, `>`, and `&` inside attacker-controlled string values must be escaped so they cannot break out of that context.* Google's own upstream Protobuf codebase encodes exactly this invariant into its C++, Java, and C# ProtoJSON writers — but the upb-based encoder used by PHP, Ruby, the standalone upb C library, and Lua bindings does not apply it, producing raw `<script>`-style payloads in JSON output whenever those environments are asked to serialize a message to ProtoJSON.

### Finding Description
In the C++ ProtoJSON writer, `MustEscape()` explicitly escapes `<`, `>`, and a list of Unicode confusables "to prevent security bugs in JavaScript," citing legacy compatibility with the ESF parser: [4](#0-3)  The Java `JsonFormat` printer implements the identical protection, with a comment stating these characters "are escaped to prevent XSS risks": [5](#0-4)  and there is a dedicated regression test (`testHtmlEscape`, referencing bug `b/73832901`) asserting `</script>` is emitted as `\u003c/script\u003e`: [6](#0-5)  The C# `JsonFormatter` mirrors this behavior via its `CommonRepresentations`/`WriteString` logic: [7](#0-6) 

In contrast, the upb-based encoder's escape table only covers the JSON-spec-mandated characters (`\n \r \t \" \f \b \\`) plus low control bytes; `<`, `>`, `&` fall through to the `default` branch and are written verbatim: [1](#0-0)  This same code is vendored (not just structurally similar, but byte-for-byte identical logic) into the PHP native extension: [8](#0-7)  and the Ruby native extension: [9](#0-8)  All three reach the public entry point `upb_JsonEncode` [10](#0-9)  which is invoked directly by Ruby's `Message#to_json`/`encode_json` binding [11](#0-10)  and by the Lua `json_encode` binding [12](#0-11) , both fully attacker-reachable via a bounded string field whose value is entirely under the calling client's control.

### Impact Explanation
An attacker who controls the content of a proto message string field (e.g., a request field later echoed back to another client, or logged/rendered by an application) can cause the PHP/Ruby/Lua ProtoJSON serializer to emit `</script>`, `<img onerror=...>`, or `&`-based payloads completely unescaped. If the consuming application follows the extremely common pattern of embedding the produced JSON literally inside an HTML `<script>` block (a pattern Protobuf's own C++/Java/C# hardening explicitly exists to protect against), the HTML tokenizer — which runs before any JavaScript parsing — will treat `</script>` as a real tag close, breaking out of the script context and enabling injected markup/script execution. This is the same class of confidentiality/integrity impact recognized in the Kibana CVE: attacker-controlled text is reflected without the escaping other equivalent code paths in the same product already provide for exactly this purpose.

### Likelihood Explanation
Likelihood is moderate-to-high in the sense that the missing check is unconditional (no feature flag disables it) and requires nothing beyond an ordinary client sending a string field value through the supported public ProtoJSON serialization API in PHP, Ruby, or via upb/Lua bindings. The remaining likelihood dependency is the consuming application's practice of inlining ProtoJSON output into an HTML/script context without an additional layer of contextual escaping — a widely-used pattern that is precisely why Google added the `<`/`>`/`&` escaping to the C++, Java, and C# writers in the first place.

### Recommendation
Port the `<`, `>`, `&` (and associated Unicode confusables/`kEscapedRanges`) escaping logic from `src/google/protobuf/json/internal/writer.cc`'s `MustEscape()` into upb's `jsonenc_put_escaped_char`/`jsonenc_stringbody` in `upb/json/encode.c`, so PHP, Ruby, Lua, and any other upb-based binding get the same HTML/JS-safety hardening as the C++, Java, and C# ProtoJSON writers.

### Proof of Concept
Using Ruby (or PHP), construct any proto message with a `string` field and set it to `"</script><script>alert(1)</script>"`, then call `msg.to_json` (Ruby) or `$msg->serializeToJsonString()` (PHP). The resulting JSON contains the literal substring `</script><script>alert(1)</script>` unescaped (verifiable by tracing `jsonenc_string` -> `jsonenc_stringbody` -> `jsonenc_put_escaped_char` in `ruby/ext/google/protobuf_c/ruby-upb.c:5560-5609` / `php/ext/google/protobuf/php-upb.c:6807-6856`), whereas the equivalent Java `TestAllTypes.newBuilder().setOptionalString("</script>")` followed by `JsonFormat.printer().print(message)` produces the escaped `\u003c/script\u003e`, as asserted by the existing `testHtmlEscape` regression test at `java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java:2016-2035`.

### Citations

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

**File:** upb/json/encode.c (L798-813)
```c
size_t upb_JsonEncode(const upb_Message* msg, const upb_MessageDef* m,
                      const upb_DefPool* ext_pool, int options, char* buf,
                      size_t size, upb_Status* status) {
  jsonenc e;

  e.buf = buf;
  e.ptr = buf;
  e.end = UPB_PTRADD(buf, size);
  e.overflow = 0;
  e.options = options;
  e.ext_pool = ext_pool;
  e.status = status;
  e.arena = NULL;

  return upb_JsonEncoder_Encode(&e, msg, m, size);
}
```

**File:** php/ext/google/protobuf/php-upb.c (L6807-6856)
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

**File:** ruby/ext/google/protobuf_c/ruby-upb.c (L5560-5609)
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

**File:** src/google/protobuf/json/internal/writer.cc (L233-269)
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
}
```

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

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L2016-2035)
```java
  // Regression test for b/73832901. Make sure html tags are escaped.
  @Test
  public void testHtmlEscape() throws Exception {
    TestAllTypes message = TestAllTypes.newBuilder().setOptionalString("</script>").build();
    assertThat(toJsonString(message))
        .isEqualTo("{\n  \"optionalString\": \"\\u003c/script\\u003e\"\n}");

    TestAllTypes.Builder builder = TestAllTypes.newBuilder();
    JsonFormat.parser().merge(toJsonString(message), builder);
    assertThat(builder.getOptionalString()).isEqualTo(message.getOptionalString());

    // Explicitly test individual HTML unsafe characters to kill any negation mutants
    TestAllTypes message2 = TestAllTypes.newBuilder().setOptionalString("\n<>&='").build();
    assertThat(toJsonString(message2))
        .isEqualTo("{\n  \"optionalString\": \"\\n\\u003c\\u003e\\u0026\\u003d\\u0027\"\n}");

    TestAllTypes.Builder builder2 = TestAllTypes.newBuilder();
    JsonFormat.parser().merge(toJsonString(message2), builder2);
    assertThat(builder2.getOptionalString()).isEqualTo(message2.getOptionalString());
  }
```

**File:** csharp/src/Google.Protobuf/JsonFormatter.cs (L61-76)
```csharp
    private static readonly string[] CommonRepresentations = {
      // C0 (ASCII and derivatives) control characters
      "\\u0000", "\\u0001", "\\u0002", "\\u0003",  // 0x00
      "\\u0004", "\\u0005", "\\u0006", "\\u0007", "\\b", "\\t", "\\n", "\\u000b", "\\f", "\\r",
      "\\u000e", "\\u000f", "\\u0010", "\\u0011", "\\u0012", "\\u0013",  // 0x10
      "\\u0014", "\\u0015", "\\u0016", "\\u0017", "\\u0018", "\\u0019", "\\u001a", "\\u001b",
      "\\u001c", "\\u001d", "\\u001e", "\\u001f",
      // Escaping of " and \ are required by www.json.org string definition.
      // Escaping of < and > are required for HTML security.
      "", "", "\\\"", "", "", "", "", "",                                            // 0x20
      "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",                // 0x30
      "", "", "", "", "\\u003c", "", "\\u003e", "", "", "", "", "", "", "", "", "",  // 0x40
      "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",                // 0x50
      "", "", "", "", "\\\\", "", "", "", "", "", "", "", "", "", "", "",            // 0x60
      "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",                // 0x70
      "", "", "", "", "", "", "", "\\u007f",
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

**File:** lua/msg.c (L1018-1041)
```c
static int lupb_jsonencode(lua_State* L) {
  upb_Message* msg = lupb_msg_check(L, 1);
  const upb_MessageDef* m = lupb_Message_Getmsgdef(L, 1);
  int options = lupb_getoptions(L, 2);
  char buf[1024];
  size_t size;
  upb_Status status;

  upb_Status_Clear(&status);
  size = upb_JsonEncode(msg, m, NULL, options, buf, sizeof(buf), &status);
  lupb_checkstatus(L, &status);

  if (size < sizeof(buf)) {
    lua_pushlstring(L, buf, size);
  } else {
    char* ptr = malloc(size + 1);
    upb_JsonEncode(msg, m, NULL, options, ptr, size + 1, &status);
    lupb_checkstatus(L, &status);
    lua_pushlstring(L, ptr, size);
    free(ptr);
  }

  return 1;
}
```
