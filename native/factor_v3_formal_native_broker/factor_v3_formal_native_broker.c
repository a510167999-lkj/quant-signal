#include <windows.h>
#include <aclapi.h>
#include <bcrypt.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

#ifndef F3_BROKER_MANIFEST_HEADER
#define F3_BROKER_MANIFEST_HEADER "factor_v3_formal_native_broker_manifest.h"
#endif
#include F3_BROKER_MANIFEST_HEADER

#ifndef F3_BROKER_PRODUCTION_HANDOFF_READY
#define F3_BROKER_PRODUCTION_HANDOFF_READY 0
#endif

#define F3_CANDIDATE_SCHEMA "factor-v3-formal-native-broker-candidate/v1"
#define F3_RUNTIME_MANIFEST_SCHEMA "factor-v3-formal-native-broker-runtime-manifest/v1"
#define F3_SOURCE_MANIFEST_SCHEMA "factor-v3-formal-native-broker-source-manifest/v1"
#define F3_SIGNING_KEY_SLOT_ID "factor-v3-execution-authorization"
#define F3_CREDENTIAL_SLOT_ID "points-primary"
#define F3_CHILD_PROTOCOL L"factor-v3-formal-native-broker-child/v1"
#define F3_MAX_CANDIDATE_BYTES (64u * 1024u)
#define F3_MAX_MANIFEST_FILE_BYTES (64u * 1024u * 1024u)
#define F3_MAX_SECRET_SLOT_BYTES (64u * 1024u)

typedef struct HeldFile {
    HANDLE handle;
    DWORD size_low;
    DWORD size_high;
    wchar_t final_path[32768];
} HeldFile;

typedef enum CandidateAction {
    ACTION_INVALID = 0,
    ACTION_BUILD_SPEC,
    ACTION_RESUME,
    ACTION_RUN,
    ACTION_VERIFY
} CandidateAction;

static void close_held(HeldFile *held) {
    if (held != NULL && held->handle != INVALID_HANDLE_VALUE) {
        CloseHandle(held->handle);
        held->handle = INVALID_HANDLE_VALUE;
    }
}

static int is_drive_absolute(const wchar_t *path) {
    return path != NULL
        && ((path[0] >= L'A' && path[0] <= L'Z')
            || (path[0] >= L'a' && path[0] <= L'z'))
        && path[1] == L':'
        && (path[2] == L'\\' || path[2] == L'/');
}

static int canonical_path(const wchar_t *path, wchar_t *output, DWORD capacity) {
    DWORD length;
    if (!is_drive_absolute(path) || output == NULL || capacity < 4) {
        return 0;
    }
    length = GetFullPathNameW(path, capacity, output, NULL);
    if (length == 0 || length >= capacity || !is_drive_absolute(output)) {
        return 0;
    }
    return 1;
}

static int final_path_without_prefix(
    const wchar_t *path,
    wchar_t *output,
    size_t capacity
) {
    const wchar_t *start = path;
    size_t length;
    if (wcsncmp(path, L"\\\\?\\", 4) == 0) {
        start = path + 4;
    }
    length = wcslen(start);
    if (length + 1 > capacity) {
        return 0;
    }
    memcpy(output, start, (length + 1) * sizeof(wchar_t));
    return 1;
}

static int reject_reparse_chain(const wchar_t *path) {
    wchar_t full[32768];
    size_t length;
    size_t index;
    if (!canonical_path(path, full, (DWORD)(sizeof(full) / sizeof(full[0])))) {
        return 0;
    }
    length = wcslen(full);
    for (index = 3; index < length; ++index) {
        HANDLE handle;
        FILE_ATTRIBUTE_TAG_INFO tag;
        wchar_t saved;
        if (full[index] != L'\\' && full[index] != L'/') {
            continue;
        }
        saved = full[index];
        full[index] = L'\0';
        handle = CreateFileW(
            full,
            FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            NULL,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            NULL
        );
        full[index] = saved;
        if (handle == INVALID_HANDLE_VALUE) {
            return 0;
        }
        if (!GetFileInformationByHandleEx(
                handle,
                FileAttributeTagInfo,
                &tag,
                sizeof(tag)
            )
            || (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
            CloseHandle(handle);
            return 0;
        }
        CloseHandle(handle);
    }
    return 1;
}

#ifdef F3_BROKER_TESTING
static int current_token_cannot_mutate_directory(const wchar_t *path) {
    static const DWORD mutation_rights =
        FILE_ADD_FILE
        | FILE_ADD_SUBDIRECTORY
        | FILE_DELETE_CHILD
        | FILE_WRITE_EA
        | FILE_WRITE_ATTRIBUTES
        | DELETE
        | WRITE_DAC
        | WRITE_OWNER;
    BY_HANDLE_FILE_INFORMATION information;
    FILE_ATTRIBUTE_TAG_INFO tag;
    GENERIC_MAPPING mapping = {
        FILE_GENERIC_READ,
        FILE_GENERIC_WRITE,
        FILE_GENERIC_EXECUTE,
        FILE_ALL_ACCESS
    };
    BYTE privilege_buffer[4096];
    PRIVILEGE_SET *privileges = (PRIVILEGE_SET *)privilege_buffer;
    PSECURITY_DESCRIPTOR descriptor = NULL;
    PACL dacl = NULL;
    HANDLE directory = INVALID_HANDLE_VALUE;
    HANDLE primary_token = NULL;
    HANDLE impersonation_token = NULL;
    DWORD privilege_size = sizeof(privilege_buffer);
    DWORD granted = 0;
    DWORD desired = MAXIMUM_ALLOWED;
    BOOL access_status = FALSE;
    wchar_t expected[32768];
    int immutable = 0;

    if (!reject_reparse_chain(path)
        || !canonical_path(
            path,
            expected,
            (DWORD)(sizeof(expected) / sizeof(expected[0]))
        )) {
        goto cleanup;
    }
    directory = CreateFileW(
        expected,
        READ_CONTROL | FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        NULL,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        NULL
    );
    if (directory == INVALID_HANDLE_VALUE
        || !GetFileInformationByHandle(directory, &information)
        || !GetFileInformationByHandleEx(
            directory,
            FileAttributeTagInfo,
            &tag,
            sizeof(tag)
        )
        || (information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0
        || (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0
        || GetSecurityInfo(
            directory,
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION
                | GROUP_SECURITY_INFORMATION
                | DACL_SECURITY_INFORMATION,
            NULL,
            NULL,
            &dacl,
            NULL,
            &descriptor
        ) != ERROR_SUCCESS
        || dacl == NULL
        || !OpenProcessToken(
            GetCurrentProcess(),
            TOKEN_QUERY | TOKEN_DUPLICATE,
            &primary_token
        )
        || !DuplicateToken(
            primary_token,
            SecurityImpersonation,
            &impersonation_token
        )) {
        goto cleanup;
    }
    MapGenericMask(&desired, &mapping);
    if (!AccessCheck(
            descriptor,
            impersonation_token,
            desired,
            &mapping,
            privileges,
            &privilege_size,
            &granted,
            &access_status
        )
        || !access_status) {
        goto cleanup;
    }
    immutable = (granted & mutation_rights) == 0;

cleanup:
    if (impersonation_token != NULL) {
        CloseHandle(impersonation_token);
    }
    if (primary_token != NULL) {
        CloseHandle(primary_token);
    }
    if (descriptor != NULL) {
        LocalFree(descriptor);
    }
    if (directory != INVALID_HANDLE_VALUE) {
        CloseHandle(directory);
    }
    SecureZeroMemory(privilege_buffer, sizeof(privilege_buffer));
    return immutable;
}
#endif

static int open_held_file(
    const wchar_t *path,
    DWORD max_bytes,
    HeldFile *held
) {
    BY_HANDLE_FILE_INFORMATION information;
    FILE_ATTRIBUTE_TAG_INFO tag;
    wchar_t expected[32768];
    wchar_t observed[32768];
    DWORD observed_length;
    if (held == NULL || !reject_reparse_chain(path)) {
        return 0;
    }
    memset(held, 0, sizeof(*held));
    held->handle = INVALID_HANDLE_VALUE;
    if (!canonical_path(path, expected, (DWORD)(sizeof(expected) / sizeof(expected[0])))) {
        return 0;
    }
    held->handle = CreateFileW(
        expected,
        GENERIC_READ,
        FILE_SHARE_READ,
        NULL,
        OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN,
        NULL
    );
    if (held->handle == INVALID_HANDLE_VALUE) {
        return 0;
    }
    if (!GetFileInformationByHandle(held->handle, &information)
        || !GetFileInformationByHandleEx(
            held->handle,
            FileAttributeTagInfo,
            &tag,
            sizeof(tag)
        )
        || (information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0
        || (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0
        || information.nNumberOfLinks != 1
        || information.nFileSizeHigh != 0
        || information.nFileSizeLow > max_bytes) {
        close_held(held);
        return 0;
    }
    observed_length = GetFinalPathNameByHandleW(
        held->handle,
        held->final_path,
        (DWORD)(sizeof(held->final_path) / sizeof(held->final_path[0])),
        FILE_NAME_NORMALIZED | VOLUME_NAME_DOS
    );
    if (observed_length == 0
        || observed_length >= sizeof(held->final_path) / sizeof(held->final_path[0])
        || !final_path_without_prefix(
            held->final_path,
            observed,
            sizeof(observed) / sizeof(observed[0])
        )
        || _wcsicmp(expected, observed) != 0) {
        close_held(held);
        return 0;
    }
    held->size_low = information.nFileSizeLow;
    held->size_high = information.nFileSizeHigh;
    return 1;
}

static int hash_held_file(HeldFile *held, unsigned char digest[32]) {
    BCRYPT_ALG_HANDLE algorithm = NULL;
    BCRYPT_HASH_HANDLE hash = NULL;
    PUCHAR object = NULL;
    DWORD object_length = 0;
    DWORD result_length = 0;
    unsigned char buffer[64 * 1024];
    DWORD read_count;
    LARGE_INTEGER zero;
    NTSTATUS status;
    int ok = 0;
    zero.QuadPart = 0;
    if (held == NULL
        || held->handle == INVALID_HANDLE_VALUE
        || !SetFilePointerEx(held->handle, zero, NULL, FILE_BEGIN)) {
        return 0;
    }
    status = BCryptOpenAlgorithmProvider(
        &algorithm,
        BCRYPT_SHA256_ALGORITHM,
        NULL,
        0
    );
    if (status < 0) {
        goto cleanup;
    }
    status = BCryptGetProperty(
        algorithm,
        BCRYPT_OBJECT_LENGTH,
        (PUCHAR)&object_length,
        sizeof(object_length),
        &result_length,
        0
    );
    if (status < 0 || result_length != sizeof(object_length)) {
        goto cleanup;
    }
    object = (PUCHAR)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, object_length);
    if (object == NULL) {
        goto cleanup;
    }
    status = BCryptCreateHash(
        algorithm,
        &hash,
        object,
        object_length,
        NULL,
        0,
        0
    );
    if (status < 0) {
        goto cleanup;
    }
    for (;;) {
        if (!ReadFile(
                held->handle,
                buffer,
                (DWORD)sizeof(buffer),
                &read_count,
                NULL
            )) {
            goto cleanup;
        }
        if (read_count == 0) {
            break;
        }
        status = BCryptHashData(hash, buffer, read_count, 0);
        if (status < 0) {
            goto cleanup;
        }
    }
    status = BCryptFinishHash(hash, digest, 32, 0);
    if (status < 0) {
        goto cleanup;
    }
    ok = SetFilePointerEx(held->handle, zero, NULL, FILE_BEGIN) != 0;

cleanup:
    SecureZeroMemory(buffer, sizeof(buffer));
    if (hash != NULL) {
        BCryptDestroyHash(hash);
    }
    if (object != NULL) {
        SecureZeroMemory(object, object_length);
        HeapFree(GetProcessHeap(), 0, object);
    }
    if (algorithm != NULL) {
        BCryptCloseAlgorithmProvider(algorithm, 0);
    }
    return ok;
}

static int digest_matches(
    const unsigned char digest[32],
    const wchar_t *expected
) {
    static const wchar_t digits[] = L"0123456789abcdef";
    wchar_t observed[65];
    size_t index;
    if (expected == NULL || wcslen(expected) != 64) {
        return 0;
    }
    for (index = 0; index < 32; ++index) {
        observed[index * 2] = digits[digest[index] >> 4];
        observed[index * 2 + 1] = digits[digest[index] & 15];
    }
    observed[64] = L'\0';
    return wcscmp(observed, expected) == 0;
}

static int verify_held_hash(HeldFile *held, const wchar_t *expected) {
    unsigned char digest[32];
    int ok = hash_held_file(held, digest) && digest_matches(digest, expected);
    SecureZeroMemory(digest, sizeof(digest));
    return ok;
}

static int held_unchanged(HeldFile *held, const wchar_t *expected_hash) {
    BY_HANDLE_FILE_INFORMATION information;
    FILE_ATTRIBUTE_TAG_INFO tag;
    if (held == NULL
        || held->handle == INVALID_HANDLE_VALUE
        || !GetFileInformationByHandle(held->handle, &information)
        || !GetFileInformationByHandleEx(
            held->handle,
            FileAttributeTagInfo,
            &tag,
            sizeof(tag)
        )
        || information.nNumberOfLinks != 1
        || information.nFileSizeHigh != held->size_high
        || information.nFileSizeLow != held->size_low
        || (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
        return 0;
    }
    if (expected_hash != NULL && !verify_held_hash(held, expected_hash)) {
        return 0;
    }
    return 1;
}

static int read_candidate(
    HeldFile *held,
    unsigned char **raw,
    DWORD *raw_size
) {
    unsigned char *buffer;
    DWORD total = 0;
    DWORD count;
    LARGE_INTEGER zero;
    if (held == NULL || raw == NULL || raw_size == NULL || held->size_high != 0) {
        return 0;
    }
    buffer = (unsigned char *)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        (SIZE_T)held->size_low + 1
    );
    if (buffer == NULL) {
        return 0;
    }
    zero.QuadPart = 0;
    if (!SetFilePointerEx(held->handle, zero, NULL, FILE_BEGIN)) {
        HeapFree(GetProcessHeap(), 0, buffer);
        return 0;
    }
    while (total < held->size_low) {
        if (!ReadFile(
                held->handle,
                buffer + total,
                held->size_low - total,
                &count,
                NULL
            )
            || count == 0) {
            SecureZeroMemory(buffer, (SIZE_T)held->size_low + 1);
            HeapFree(GetProcessHeap(), 0, buffer);
            return 0;
        }
        total += count;
    }
    if (!SetFilePointerEx(held->handle, zero, NULL, FILE_BEGIN)) {
        SecureZeroMemory(buffer, (SIZE_T)held->size_low + 1);
        HeapFree(GetProcessHeap(), 0, buffer);
        return 0;
    }
    *raw = buffer;
    *raw_size = total;
    return 1;
}

static int exact_line(
    const unsigned char *line,
    size_t line_length,
    const char *name,
    const char **value,
    size_t *value_length
) {
    size_t name_length = strlen(name);
    if (line_length <= name_length
        || memcmp(line, name, name_length) != 0
        || line[name_length] != '=') {
        return 0;
    }
    *value = (const char *)line + name_length + 1;
    *value_length = line_length - name_length - 1;
    return 1;
}

static int value_equals(const char *value, size_t length, const char *expected) {
    size_t expected_length = strlen(expected);
    return length == expected_length && memcmp(value, expected, length) == 0;
}

static int candidate_absolute_path(const char *value, size_t length) {
    wchar_t normalized[32768];
    wchar_t *wide = NULL;
    int wide_length;
    int converted;
    int ok = 0;
    if (value == NULL || length == 0 || length > INT_MAX) {
        return 0;
    }
    wide_length = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        value,
        (int)length,
        NULL,
        0
    );
    if (wide_length <= 0 || wide_length >= 32768) {
        return 0;
    }
    wide = (wchar_t *)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        ((SIZE_T)wide_length + 1) * sizeof(wchar_t)
    );
    if (wide == NULL) {
        return 0;
    }
    converted = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        value,
        (int)length,
        wide,
        wide_length
    );
    if (converted == wide_length
        && canonical_path(
            wide,
            normalized,
            (DWORD)(sizeof(normalized) / sizeof(normalized[0]))
        )
        && wcscmp(wide, normalized) == 0) {
        ok = 1;
    }
    SecureZeroMemory(wide, ((SIZE_T)wide_length + 1) * sizeof(wchar_t));
    HeapFree(GetProcessHeap(), 0, wide);
    return ok;
}

static int parse_candidate(
    const unsigned char *raw,
    DWORD raw_size,
    CandidateAction *action
) {
    static const char *names[] = {
        "schema",
        "action",
        "authorization_path",
        "completion_marker_path",
        "publication_receipt_path",
        "execution_ledger_root",
        "resume_authorization_path",
        "resume_status_path",
        "signing_key_slot_id",
        "credential_slot_id",
        "runtime_manifest_schema",
        "source_manifest_schema"
    };
    const char *values[12];
    size_t lengths[12];
    size_t offset = 0;
    size_t field;
    int wide_length;
    CandidateAction observed = ACTION_INVALID;
    if (raw == NULL || action == NULL || raw_size == 0 || raw[raw_size - 1] != '\n') {
        return 0;
    }
    for (field = 0; field < raw_size; ++field) {
        if (raw[field] == '\0' || raw[field] == '\r') {
            return 0;
        }
    }
    wide_length = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        (const char *)raw,
        (int)raw_size,
        NULL,
        0
    );
    if (wide_length <= 0) {
        return 0;
    }
    for (field = 0; field < 12; ++field) {
        size_t end = offset;
        while (end < raw_size && raw[end] != '\n') {
            ++end;
        }
        if (end >= raw_size
            || !exact_line(
                raw + offset,
                end - offset,
                names[field],
                &values[field],
                &lengths[field]
            )) {
            return 0;
        }
        offset = end + 1;
    }
    if (offset != raw_size
        || !value_equals(values[0], lengths[0], F3_CANDIDATE_SCHEMA)
        || lengths[2] == 0
        || lengths[3] == 0
        || lengths[4] == 0
        || lengths[5] == 0
        || !candidate_absolute_path(values[2], lengths[2])
        || !candidate_absolute_path(values[3], lengths[3])
        || !candidate_absolute_path(values[4], lengths[4])
        || !candidate_absolute_path(values[5], lengths[5])
        || !value_equals(values[8], lengths[8], F3_SIGNING_KEY_SLOT_ID)
        || !value_equals(values[10], lengths[10], F3_RUNTIME_MANIFEST_SCHEMA)
        || !value_equals(values[11], lengths[11], F3_SOURCE_MANIFEST_SCHEMA)) {
        return 0;
    }
    if (value_equals(values[1], lengths[1], "build-spec")) {
        observed = ACTION_BUILD_SPEC;
    } else if (value_equals(values[1], lengths[1], "resume")) {
        observed = ACTION_RESUME;
    } else if (value_equals(values[1], lengths[1], "run")) {
        observed = ACTION_RUN;
    } else if (value_equals(values[1], lengths[1], "verify")) {
        observed = ACTION_VERIFY;
    } else {
        return 0;
    }
    if (observed == ACTION_RESUME) {
        if (lengths[6] == 0
            || lengths[7] == 0
            || !candidate_absolute_path(values[6], lengths[6])
            || !candidate_absolute_path(values[7], lengths[7])
            || !value_equals(values[9], lengths[9], F3_CREDENTIAL_SLOT_ID)) {
            return 0;
        }
    } else {
        if (lengths[6] != 0 || lengths[7] != 0) {
            return 0;
        }
        if (observed == ACTION_RUN) {
            if (!value_equals(values[9], lengths[9], F3_CREDENTIAL_SLOT_ID)) {
                return 0;
            }
        } else if (!value_equals(values[9], lengths[9], "none")) {
            return 0;
        }
    }
    *action = observed;
    return 1;
}

static int filename_matches_candidate_digest(
    const wchar_t *path,
    const unsigned char digest[32]
) {
    static const wchar_t suffix[] = L".candidate";
    const wchar_t *filename = wcsrchr(path, L'\\');
    const wchar_t *alternate = wcsrchr(path, L'/');
    wchar_t expected[65];
    static const wchar_t digits[] = L"0123456789abcdef";
    size_t index;
    if (alternate != NULL && (filename == NULL || alternate > filename)) {
        filename = alternate;
    }
    filename = filename == NULL ? path : filename + 1;
    if (wcslen(filename) != 64 + wcslen(suffix)) {
        return 0;
    }
    for (index = 0; index < 32; ++index) {
        expected[index * 2] = digits[digest[index] >> 4];
        expected[index * 2 + 1] = digits[digest[index] & 15];
    }
    expected[64] = L'\0';
    return wcsncmp(filename, expected, 64) == 0
        && wcscmp(filename + 64, suffix) == 0;
}

static int manifests_provisioned(void) {
    return F3_BROKER_RUNTIME_PATH[0] != L'\0'
        && wcslen(F3_BROKER_RUNTIME_SHA256) == 64
        && F3_BROKER_SOURCE_PATH[0] != L'\0'
        && wcslen(F3_BROKER_SOURCE_SHA256) == 64
        && F3_BROKER_SIGNING_KEY_SLOT_PATH[0] != L'\0'
        && F3_BROKER_CREDENTIAL_SLOT_PATH[0] != L'\0';
}

static int open_and_validate_public_boundary(
    const wchar_t *candidate_path,
    HeldFile *runtime,
    HeldFile *source,
    HeldFile *candidate,
    CandidateAction *action
) {
    unsigned char candidate_digest[32];
    unsigned char *raw = NULL;
    DWORD raw_size = 0;
    int ok = 0;
    if (!manifests_provisioned()
        || !open_held_file(
            F3_BROKER_RUNTIME_PATH,
            F3_MAX_MANIFEST_FILE_BYTES,
            runtime
        )
        || !verify_held_hash(runtime, F3_BROKER_RUNTIME_SHA256)
        || !open_held_file(
            F3_BROKER_SOURCE_PATH,
            F3_MAX_MANIFEST_FILE_BYTES,
            source
        )
        || !verify_held_hash(source, F3_BROKER_SOURCE_SHA256)
        || !open_held_file(candidate_path, F3_MAX_CANDIDATE_BYTES, candidate)
        || !hash_held_file(candidate, candidate_digest)
        || !filename_matches_candidate_digest(candidate_path, candidate_digest)
        || !read_candidate(candidate, &raw, &raw_size)
        || !parse_candidate(raw, raw_size, action)) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    SecureZeroMemory(candidate_digest, sizeof(candidate_digest));
    if (raw != NULL) {
        SecureZeroMemory(raw, (SIZE_T)raw_size + 1);
        HeapFree(GetProcessHeap(), 0, raw);
    }
    return ok;
}

#ifdef F3_BROKER_TESTING
static int append_environment_entry(
    wchar_t *block,
    size_t capacity,
    size_t *offset,
    const wchar_t *name,
    const wchar_t *value
) {
    size_t name_length = wcslen(name);
    size_t value_length = wcslen(value);
    size_t needed = name_length + 1 + value_length + 1;
    if (*offset + needed + 1 > capacity) {
        return 0;
    }
    memcpy(block + *offset, name, name_length * sizeof(wchar_t));
    *offset += name_length;
    block[(*offset)++] = L'=';
    memcpy(block + *offset, value, value_length * sizeof(wchar_t));
    *offset += value_length;
    block[(*offset)++] = L'\0';
    return 1;
}

static wchar_t *sanitized_environment(void) {
    const size_t capacity = 32768;
    wchar_t *block = (wchar_t *)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        capacity * sizeof(wchar_t)
    );
    size_t offset = 0;
    wchar_t windows_directory[32768];
    UINT windows_directory_length;
    if (block == NULL) {
        return NULL;
    }
    if (!append_environment_entry(
            block,
            capacity,
            &offset,
            L"FACTOR_V3_FORMAL_NATIVE_BROKER_PROTOCOL",
            F3_CHILD_PROTOCOL
        )) {
        HeapFree(GetProcessHeap(), 0, block);
        return NULL;
    }
    windows_directory_length = GetSystemWindowsDirectoryW(
        windows_directory,
        (UINT)(sizeof(windows_directory) / sizeof(windows_directory[0]))
    );
    if (windows_directory_length == 0
        || windows_directory_length
            >= sizeof(windows_directory) / sizeof(windows_directory[0])
        || !append_environment_entry(
            block,
            capacity,
            &offset,
            L"SYSTEMROOT",
            windows_directory
        )
        || !append_environment_entry(
            block,
            capacity,
            &offset,
            L"WINDIR",
            windows_directory
        )) {
        SecureZeroMemory(windows_directory, sizeof(windows_directory));
        SecureZeroMemory(block, capacity * sizeof(wchar_t));
        HeapFree(GetProcessHeap(), 0, block);
        return NULL;
    }
    SecureZeroMemory(windows_directory, sizeof(windows_directory));
    block[offset] = L'\0';
    return block;
}

static int parent_directory(
    const wchar_t *path,
    wchar_t *output,
    size_t capacity
) {
    wchar_t *separator;
    if (capacity > UINT_MAX
        || !canonical_path(path, output, (DWORD)capacity)) {
        return 0;
    }
    separator = wcsrchr(output, L'\\');
    if (separator == NULL || separator <= output + 2) {
        return 0;
    }
    *separator = L'\0';
    return 1;
}

static int quoted_command_line(
    const wchar_t *executable,
    const wchar_t *argument,
    wchar_t *output,
    size_t capacity
) {
    int result;
    if (wcschr(executable, L'"') != NULL || wcschr(argument, L'"') != NULL) {
        return 0;
    }
    result = _snwprintf_s(
        output,
        capacity,
        _TRUNCATE,
        L"\"%ls\" \"%ls\"",
        executable,
        argument
    );
    return result > 0;
}

static int launch_test_child(
    const wchar_t *output_path,
    int close_job_after_child_ready
) {
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits;
    HANDLE job = NULL;
    wchar_t command_line[32768];
    wchar_t runtime_directory[32768];
    wchar_t *environment = NULL;
    DWORD exit_code = 1;
    int ok = 0;
    memset(&startup, 0, sizeof(startup));
    memset(&process, 0, sizeof(process));
    memset(&limits, 0, sizeof(limits));
    startup.cb = sizeof(startup);
    if (!quoted_command_line(
            F3_BROKER_RUNTIME_PATH,
            output_path,
            command_line,
            sizeof(command_line) / sizeof(command_line[0])
        )
        || !parent_directory(
            F3_BROKER_RUNTIME_PATH,
            runtime_directory,
            sizeof(runtime_directory) / sizeof(runtime_directory[0])
        )) {
        goto cleanup;
    }
    environment = sanitized_environment();
    if (environment == NULL) {
        goto cleanup;
    }
    job = CreateJobObjectW(NULL, NULL);
    if (job == NULL) {
        goto cleanup;
    }
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &limits,
            sizeof(limits)
        )
        || !CreateProcessW(
            F3_BROKER_RUNTIME_PATH,
            command_line,
            NULL,
            NULL,
            FALSE,
            CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
            environment,
            runtime_directory,
            &startup,
            &process
        )
        || !AssignProcessToJobObject(job, process.hProcess)
        || ResumeThread(process.hThread) == (DWORD)-1) {
        if (process.hProcess != NULL) {
            TerminateProcess(process.hProcess, 90);
        }
        goto cleanup;
    }
    if (close_job_after_child_ready) {
        DWORD elapsed = 0;
        while (GetFileAttributesW(output_path) == INVALID_FILE_ATTRIBUTES
            && elapsed < 5000) {
            Sleep(20);
            elapsed += 20;
        }
        if (GetFileAttributesW(output_path) == INVALID_FILE_ATTRIBUTES) {
            TerminateProcess(process.hProcess, 91);
            goto cleanup;
        }
        CloseHandle(job);
        job = NULL;
        if (WaitForSingleObject(process.hProcess, 5000) != WAIT_OBJECT_0
            || !GetExitCodeProcess(process.hProcess, &exit_code)
            || exit_code == STILL_ACTIVE) {
            TerminateProcess(process.hProcess, 92);
            goto cleanup;
        }
    } else if (WaitForSingleObject(process.hProcess, 30000) != WAIT_OBJECT_0
        || !GetExitCodeProcess(process.hProcess, &exit_code)
        || exit_code != 0) {
        TerminateProcess(process.hProcess, 93);
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (process.hThread != NULL) {
        CloseHandle(process.hThread);
    }
    if (process.hProcess != NULL) {
        CloseHandle(process.hProcess);
    }
    if (job != NULL) {
        CloseHandle(job);
    }
    if (environment != NULL) {
        SecureZeroMemory(environment, 32768 * sizeof(wchar_t));
        HeapFree(GetProcessHeap(), 0, environment);
    }
    SecureZeroMemory(command_line, sizeof(command_line));
    SecureZeroMemory(runtime_directory, sizeof(runtime_directory));
    return ok;
}
#endif

static int validate_candidate_only(const wchar_t *candidate_path) {
    HeldFile runtime = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile source = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile candidate = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    CandidateAction action = ACTION_INVALID;
    int ok = open_and_validate_public_boundary(
        candidate_path,
        &runtime,
        &source,
        &candidate,
        &action
    );
    if (ok) {
        ok = held_unchanged(&candidate, NULL)
            && held_unchanged(&source, F3_BROKER_SOURCE_SHA256)
            && held_unchanged(&runtime, F3_BROKER_RUNTIME_SHA256);
    }
    close_held(&candidate);
    close_held(&source);
    close_held(&runtime);
    return ok;
}

#ifdef F3_BROKER_TESTING
static int test_launch(
    const wchar_t *candidate_path,
    const wchar_t *output_path,
    int close_job_after_child_ready
) {
    HeldFile runtime = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile source = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile candidate = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile signing_key = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile credential = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    CandidateAction action = ACTION_INVALID;
    int ok = open_and_validate_public_boundary(
        candidate_path,
        &runtime,
        &source,
        &candidate,
        &action
    );
    if (!ok
        || !open_held_file(
            F3_BROKER_SIGNING_KEY_SLOT_PATH,
            F3_MAX_SECRET_SLOT_BYTES,
            &signing_key
        )
        || ((action == ACTION_RUN || action == ACTION_RESUME)
            && !open_held_file(
                F3_BROKER_CREDENTIAL_SLOT_PATH,
                F3_MAX_SECRET_SLOT_BYTES,
                &credential
            ))) {
        ok = 0;
        goto cleanup;
    }
    ok = launch_test_child(output_path, close_job_after_child_ready);
    if (ok) {
        ok = held_unchanged(&candidate, NULL)
            && held_unchanged(&source, F3_BROKER_SOURCE_SHA256)
            && held_unchanged(&runtime, F3_BROKER_RUNTIME_SHA256)
            && held_unchanged(&signing_key, NULL)
            && (credential.handle == INVALID_HANDLE_VALUE
                || held_unchanged(&credential, NULL));
    }

cleanup:
    close_held(&credential);
    close_held(&signing_key);
    close_held(&candidate);
    close_held(&source);
    close_held(&runtime);
    return ok;
}
#endif

int wmain(int argc, wchar_t **argv) {
    if (argc == 3 && wcscmp(argv[1], L"--validate-candidate") == 0) {
        if (!validate_candidate_only(argv[2])) {
            fwprintf(stderr, L"native broker candidate rejected\n");
            return 20;
        }
        return 0;
    }
#ifdef F3_BROKER_TESTING
    if (
        argc == 3
        && wcscmp(argv[1], L"--test-current-token-readonly-root") == 0
    ) {
        if (!current_token_cannot_mutate_directory(argv[2])) {
            fwprintf(stderr, L"native broker namespace mutable by caller\n");
            return 25;
        }
        return 0;
    }
    if (argc == 4 && wcscmp(argv[1], L"--test-launch") == 0) {
        if (!test_launch(argv[2], argv[3], 0)) {
            fwprintf(stderr, L"native broker test boundary rejected\n");
            return 21;
        }
        return 0;
    }
    if (argc == 4 && wcscmp(argv[1], L"--test-job-kill") == 0) {
        if (!test_launch(argv[2], argv[3], 1)) {
            fwprintf(stderr, L"native broker job boundary rejected\n");
            return 24;
        }
        return 0;
    }
#endif
    if (argc == 3 && wcscmp(argv[1], L"--launch") == 0) {
#if F3_BROKER_PRODUCTION_HANDOFF_READY
        fwprintf(stderr, L"native broker service/CNG handoff is unimplemented\n");
#else
        fwprintf(stderr, L"native broker production boundary is unprovisioned\n");
#endif
        return 22;
    }
    fwprintf(stderr, L"native broker invocation rejected\n");
    return 23;
}
