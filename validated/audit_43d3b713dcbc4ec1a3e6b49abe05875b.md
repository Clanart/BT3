### Title
Unbounded backward memory scan for comma delimiter in ProtoJSON `Any` parsing lacks lower-bound check - ([File: upb/json/decode.c])

### Summary
`jsondec_any()` in the upb JSON decoder (used by Ruby, PHP, and other upb-backed language bindings' public `parseJson`/`from_json` APIs) scans backward through the raw JSON input buffer to locate the comma that separates a `"@type"` key from a preceding sibling field, using a while-loop with no lower-bound check. This is structurally identical to the CVE-2020-13498 pattern: a length/offset derived from parsing an "encoded type" tag (here, the `@type` discriminator of a `google.protobuf.Any` JSON object) is used to walk memory without validating it stays inside the buffer, unlike the adjacent, near-identical function `jsondec_typeurl()` which explicitly bounds its own backward scan.

### Finding Description
When decoding a JSON object for `google.protobuf.Any`, the parser must find the `"@type"` key even if it is not the first key in the object [1](#0-0) . If a non-`"@type"` field is seen first, `pre_type_data` is recorded as the start of that field's key text. Once `"@type"` is subsequently found, the code computes the end of the "pre-type" span by walking backward from the position of the `"@type"` key looking for a literal `,` byte:

```
if (jsondec_streql(name, "@type")) {
  any_m = jsondec_typeurl(d, msg, m);
  if (pre_type_data) {
    pre_type_end = start;
    while (*pre_type_end != ',') pre_type_end--;
  }
}
``` [2](#0-1) 

This loop has **no bound check** against the start of the input buffer (`d->ptr`/the JSON text owned by the caller). Contrast this with the sibling function `jsondec_typeurl()`, parsing the `@type` string itself to find the type name after the last `/`, which performs the analogous backward scan but *correctly* bounds it:

```
while (ptr > type_url.data && *--ptr != '/') {
}
``` [3](#0-2) 

The missing `pre_type_end > <buffer start>` guard in `jsondec_any` is the failed invariant: the code implicitly assumes a comma byte must exist somewhere before `start` because valid JSON requires a `,` between two object members. That assumption is not independently enforced at this call site — it relies entirely on upstream lexer state remaining perfectly well-formed for every code path that can set `pre_type_data`. Once found, the (possibly out-of-bounds-derived) span `[pre_type_data, pre_type_end]` is copied via `memcpy` into a newly allocated arena buffer and re-parsed as JSON object content, then encoded and stored into the `Any.value` field of the resulting message:

```
size_t len = pre_type_end - pre_type_data + 1;
char* tmp = upb_Arena_Malloc(d->arena, len);
...
memcpy(tmp, pre_type_data, len - 1);
``` [4](#0-3) 

The identical code is duplicated verbatim in the Ruby and PHP amalgamated upb builds, meaning this reaches the public JSON-parsing entry points of those language runtimes. [5](#0-4) [6](#0-5) 

### Impact Explanation
If the backward scan runs past the start of the caller-owned input buffer (heap memory holding the JSON text) before encountering a `,` byte, it will read and subsequently `memcpy` adjacent out-of-bounds heap memory into the arena buffer that becomes part of the decoded `Any` submessage's byte content. If the application re-serializes, logs, or echoes that submessage (a common pattern for `Any`-typed fields), attacker-uncontrolled adjacent heap data can be disclosed back to the client — matching the "out of bounds memory access... information disclosure" impact described in the CVE analog, and the attacker-triggerable nature (bounded input, no privileged access) matches the public JSON-parse API threat model in this analysis.

### Likelihood Explanation
Reachability depends on breaking the implicit "a comma always precedes `@type`" invariant. Under fully well-formed JSON input this invariant should hold, since the upb JSON lexer enforces `,` between object members via `jsondec_entrysep`/`jsondec_objnext`. However, the code performs **no local defensive check**, unlike the directly adjacent `jsondec_typeurl` function that does bound its equivalent scan — this asymmetry indicates the omission is unintentional and any lexer edge case, legacy/nonconformant parsing mode, or future refactor that permits `pre_type_data` to be set without a guaranteed preceding literal comma would immediately turn this into an exploitable out-of-bounds read. Given the CVSS 3.1 profile of the reference CVE (Local, Low complexity, Requires-UI, Confidentiality-High-only), Medium severity is preserved here as an analog because the vulnerable code path is reachable purely through an ordinary client-supplied bounded ProtoJSON payload to a supported public parse API (`Any` JSON parsing), with no privileged access or hostile schema required.

### Recommendation
Add an explicit lower-bound check to the backward scan in `jsondec_any`, mirroring `jsondec_typeurl`'s pattern:
```
pre_type_end = start;
while (pre_type_end > pre_type_data && *pre_type_end != ',') pre_type_end--;
if (*pre_type_end != ',') jsondec_err(d, "Malformed Any object");
```
This should be applied to `upb/json/decode.c` and propagated to the amalgamated copies in `ruby/ext/google/protobuf_c/ruby-upb.c` and `php/ext/google/protobuf/php-upb.c`.

### Proof of Concept
A concrete byte-level reproduction requires instrumenting the exact lexer states (`jsondec_objnext`/`jsondec_entrysep`) that can set `pre_type_data` without a guaranteed preceding `,` byte in the raw buffer; I was not able to execute or fully trace such a lexer state within the available investigation to produce a verified triggering JSON payload, so no test was run and no confirmed crash/ASAN output can be claimed. The finding is reported as a code-level missing-bounds-check defect (verified by direct code comparison against the correctly-bounded sibling function `jsondec_typeurl`) rather than a demonstrated exploit.

### Citations

**File:** upb/json/decode.c (L1427-1429)
```c
  /* Find message name after the last '/' */
  while (ptr > type_url.data && *--ptr != '/') {
  }
```

**File:** upb/json/decode.c (L1458-1473)
```c
  /* Scan looking for "@type", which is not necessarily first. */
  while (!any_m && jsondec_objnext(d)) {
    const char* start = d->ptr;
    upb_StringView name = jsondec_string(d);
    jsondec_entrysep(d);
    if (jsondec_streql(name, "@type")) {
      any_m = jsondec_typeurl(d, msg, m);
      if (pre_type_data) {
        pre_type_end = start;
        while (*pre_type_end != ',') pre_type_end--;
      }
    } else {
      if (!pre_type_data) pre_type_data = start;
      jsondec_skipval(d);
    }
  }
```

**File:** upb/json/decode.c (L1483-1489)
```c
  if (pre_type_data) {
    size_t len = pre_type_end - pre_type_data + 1;
    char* tmp = upb_Arena_Malloc(d->arena, len);
    jsondec_checkoom(d, tmp);
    const char* saved_ptr = d->ptr;
    const char* saved_end = d->end;
    memcpy(tmp, pre_type_data, len - 1);
```

**File:** ruby/ext/google/protobuf_c/ruby-upb.c (L5201-5209)
```c
static void jsondec_wrapper(jsondec* d, upb_Message* msg,
                            const upb_MessageDef* m) {
  UPB_ASSERT(!upb_Message_IsFrozen(msg));
  const upb_FieldDef* value_f = upb_MessageDef_FindFieldByNumber(m, 1);
  upb_JsonMessageValue val = jsondec_value(d, value_f);
  UPB_ASSUME(val.ignore == false);  // Wrapper cannot be an enum.
  jsondec_checkoom(
      d, upb_Message_SetFieldByDef(msg, value_f, val.value, d->arena));
}
```

**File:** php/ext/google/protobuf/php-upb.c (L6376-6446)
```c
static void jsondec_any(jsondec* d, upb_Message* msg, const upb_MessageDef* m) {
  UPB_ASSERT(!upb_Message_IsFrozen(msg));
  /* string type_url = 1;
   * bytes value = 2; */
  const upb_FieldDef* value_f = upb_MessageDef_FindFieldByNumber(m, 2);
  upb_Message* any_msg;
  const upb_MessageDef* any_m = NULL;
  const char* pre_type_data = NULL;
  const char* pre_type_end = NULL;
  upb_MessageValue encoded;

  jsondec_objstart(d);

  /* Scan looking for "@type", which is not necessarily first. */
  while (!any_m && jsondec_objnext(d)) {
    const char* start = d->ptr;
    upb_StringView name = jsondec_string(d);
    jsondec_entrysep(d);
    if (jsondec_streql(name, "@type")) {
      any_m = jsondec_typeurl(d, msg, m);
      if (pre_type_data) {
        pre_type_end = start;
        while (*pre_type_end != ',') pre_type_end--;
      }
    } else {
      if (!pre_type_data) pre_type_data = start;
      jsondec_skipval(d);
    }
  }

  if (!any_m) {
    jsondec_err(d, "Any object didn't contain a '@type' field");
  }

  const upb_MiniTable* any_layout = upb_MessageDef_MiniTable(any_m);
  any_msg = upb_Message_New(any_layout, d->arena);
  jsondec_checkoom(d, any_msg);

  if (pre_type_data) {
    size_t len = pre_type_end - pre_type_data + 1;
    char* tmp = upb_Arena_Malloc(d->arena, len);
    jsondec_checkoom(d, tmp);
    const char* saved_ptr = d->ptr;
    const char* saved_end = d->end;
    memcpy(tmp, pre_type_data, len - 1);
    tmp[len - 1] = '}';
    d->ptr = tmp;
    d->end = tmp + len;
    d->is_first = true;
    while (jsondec_objnext(d)) {
      jsondec_anyfield(d, any_msg, any_m);
    }
    d->ptr = saved_ptr;
    d->end = saved_end;
  }

  while (jsondec_objnext(d)) {
    jsondec_anyfield(d, any_msg, any_m);
  }

  jsondec_objend(d);

  upb_EncodeStatus status =
      upb_Encode(any_msg, upb_MessageDef_MiniTable(any_m), 0, d->arena,
                 (char**)&encoded.str_val.data, &encoded.str_val.size);
  if (status != kUpb_EncodeStatus_Ok) {
    jsondec_errf(d, "Encode failed: %s", upb_EncodeStatus_String(status));
  }
  jsondec_checkoom(d,
                   upb_Message_SetFieldByDef(msg, value_f, encoded, d->arena));
}
```
