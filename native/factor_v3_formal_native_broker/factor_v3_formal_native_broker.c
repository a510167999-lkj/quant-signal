#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif

#include <windows.h>
#include <aclapi.h>
#include <bcrypt.h>
#include <limits.h>
#include <ncrypt.h>
#include <sddl.h>
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

#ifdef F3_BROKER_TESTING
#ifndef F3_BROKER_DISPOSABLE_TEST_MANIFEST
#define F3_BROKER_DISPOSABLE_TEST_MANIFEST 0
#endif
#if F3_BROKER_DISPOSABLE_TEST_MANIFEST != 1
#error F3_BROKER_TESTING requires a disposable test manifest
#endif
#if F3_BROKER_PRODUCTION_HANDOFF_READY != 0
#error F3_BROKER_TESTING is forbidden in a production-ready build
#endif
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
#define F3_RSA_BITS 2048u
#define F3_DISPOSABLE_KEY_PREFIX L"quant-signal-lkj-disposable-test-"

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

static int sid_matches_well_known(PSID sid, WELL_KNOWN_SID_TYPE kind) {
    BYTE buffer[SECURITY_MAX_SID_SIZE];
    DWORD length = sizeof(buffer);
    return CreateWellKnownSid(kind, NULL, buffer, &length)
        && EqualSid(sid, buffer);
}

static int sid_matches_string(PSID sid, const wchar_t *expected) {
    PSID parsed = NULL;
    int matches = 0;
    if (expected != NULL
        && expected[0] != L'\0'
        && ConvertStringSidToSidW(expected, &parsed)) {
        matches = EqualSid(sid, parsed) != 0;
    }
    if (parsed != NULL) {
        LocalFree(parsed);
    }
    return matches;
}

static int trusted_namespace_owner(PSID owner) {
    static const wchar_t trusted_installer_sid[] =
        L"S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464";
    return owner != NULL
        && IsValidSid(owner)
        && (
            sid_matches_well_known(owner, WinLocalSystemSid)
            || sid_matches_well_known(owner, WinBuiltinAdministratorsSid)
            || sid_matches_string(owner, trusted_installer_sid)
            || sid_matches_string(owner, F3_BROKER_SERVICE_SID)
        );
}

int f3_broker_current_token_cannot_mutate_directory(const wchar_t *path) {
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
    PSID owner = NULL;
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
            &owner,
            NULL,
            &dacl,
            NULL,
            &descriptor
        ) != ERROR_SUCCESS
        || dacl == NULL
        || !trusted_namespace_owner(owner)
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

int f3_broker_current_process_is_expected_service(void) {
    HANDLE token = NULL;
    PSID service_sid = NULL;
    BYTE interactive_buffer[SECURITY_MAX_SID_SIZE];
    DWORD interactive_size = sizeof(interactive_buffer);
    DWORD session_id = 1;
    DWORD returned = 0;
    BOOL service_member = FALSE;
    BOOL interactive_member = TRUE;
    int ok = 0;
    if (F3_BROKER_SERVICE_SID[0] == L'\0'
        || !ConvertStringSidToSidW(
            F3_BROKER_SERVICE_SID,
            &service_sid
        )
        || !CreateWellKnownSid(
            WinInteractiveSid,
            NULL,
            interactive_buffer,
            &interactive_size
        )
        || !OpenProcessToken(
            GetCurrentProcess(),
            TOKEN_QUERY,
            &token
        )
        || !GetTokenInformation(
            token,
            TokenSessionId,
            &session_id,
            sizeof(session_id),
            &returned
        )
        || returned != sizeof(session_id)
        || !CheckTokenMembership(token, service_sid, &service_member)
        || !CheckTokenMembership(
            token,
            interactive_buffer,
            &interactive_member
        )) {
        goto cleanup;
    }
    ok = session_id == 0 && service_member && !interactive_member;

cleanup:
    if (token != NULL) {
        CloseHandle(token);
    }
    if (service_sid != NULL) {
        LocalFree(service_sid);
    }
    SecureZeroMemory(interactive_buffer, sizeof(interactive_buffer));
    return ok;
}

SECURITY_STATUS f3_broker_open_cng_provider(NCRYPT_PROV_HANDLE *provider) {
    if (provider == NULL) {
        return NTE_INVALID_PARAMETER;
    }
    *provider = 0;
    return NCryptOpenStorageProvider(
        provider,
        F3_BROKER_CNG_PROVIDER,
        0
    );
}

SECURITY_STATUS f3_broker_sign_sha256_with_cng_key(
    NCRYPT_KEY_HANDLE key,
    const unsigned char digest[32],
    unsigned char **signature,
    DWORD *signature_size
) {
    BCRYPT_PKCS1_PADDING_INFO padding = {BCRYPT_SHA256_ALGORITHM};
    SECURITY_STATUS status;
    DWORD required = 0;
    unsigned char *buffer = NULL;
    if (key == 0
        || digest == NULL
        || signature == NULL
        || signature_size == NULL) {
        return NTE_INVALID_PARAMETER;
    }
    *signature = NULL;
    *signature_size = 0;
    status = NCryptSignHash(
        key,
        &padding,
        (PBYTE)digest,
        32,
        NULL,
        0,
        &required,
        NCRYPT_PAD_PKCS1_FLAG
    );
    if (status != ERROR_SUCCESS || required == 0) {
        return status == ERROR_SUCCESS ? NTE_INTERNAL_ERROR : status;
    }
    buffer = (unsigned char *)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        required
    );
    if (buffer == NULL) {
        return NTE_NO_MEMORY;
    }
    status = NCryptSignHash(
        key,
        &padding,
        (PBYTE)digest,
        32,
        buffer,
        required,
        &required,
        NCRYPT_PAD_PKCS1_FLAG
    );
    if (status != ERROR_SUCCESS) {
        SecureZeroMemory(buffer, required);
        HeapFree(GetProcessHeap(), 0, buffer);
        return status;
    }
    *signature = buffer;
    *signature_size = required;
    return ERROR_SUCCESS;
}

#ifdef F3_BROKER_TESTING
static int cng_key_absent(const wchar_t *key_name) {
    NCRYPT_PROV_HANDLE provider = 0;
    NCRYPT_KEY_HANDLE key = 0;
    SECURITY_STATUS status;
    int absent = 0;
    if (f3_broker_open_cng_provider(&provider) != ERROR_SUCCESS) {
        return 0;
    }
    status = NCryptOpenKey(provider, &key, key_name, 0, 0);
    absent = status == NTE_BAD_KEYSET;
    if (key != 0) {
        NCryptFreeObject(key);
    }
    NCryptFreeObject(provider);
    return absent;
}

static int disposable_cng_sign_test(const wchar_t *key_name) {
    static const unsigned char digest[32] = {
        0x62, 0x53, 0x4d, 0x7e, 0x5f, 0xe3, 0x2d, 0x82,
        0xca, 0x35, 0x6b, 0x19, 0x26, 0x92, 0x7f, 0x9a,
        0x6a, 0x75, 0x38, 0xec, 0x1b, 0x35, 0x67, 0x2d,
        0xa2, 0x65, 0xa7, 0x00, 0x29, 0x22, 0x7f, 0x8d
    };
    BCRYPT_PKCS1_PADDING_INFO padding = {BCRYPT_SHA256_ALGORITHM};
    NCRYPT_PROV_HANDLE provider = 0;
    NCRYPT_KEY_HANDLE key = 0;
    unsigned char *signature = NULL;
    DWORD signature_size = 0;
    DWORD bits = F3_RSA_BITS;
    DWORD export_policy = 0;
    DWORD ignored = 0;
    SECURITY_STATUS status;
    int deleted = 0;
    int ok = 0;
    if (key_name == NULL
        || wcsncmp(
            key_name,
            F3_DISPOSABLE_KEY_PREFIX,
            wcslen(F3_DISPOSABLE_KEY_PREFIX)
        ) != 0
        || !cng_key_absent(key_name)
        || f3_broker_open_cng_provider(&provider) != ERROR_SUCCESS
        || NCryptCreatePersistedKey(
            provider,
            &key,
            F3_BROKER_CNG_ALGORITHM,
            key_name,
            0,
            NCRYPT_OVERWRITE_KEY_FLAG
        ) != ERROR_SUCCESS
        || NCryptSetProperty(
            key,
            NCRYPT_LENGTH_PROPERTY,
            (PBYTE)&bits,
            sizeof(bits),
            0
        ) != ERROR_SUCCESS
        || NCryptSetProperty(
            key,
            NCRYPT_EXPORT_POLICY_PROPERTY,
            (PBYTE)&export_policy,
            sizeof(export_policy),
            0
        ) != ERROR_SUCCESS
        || NCryptFinalizeKey(key, 0) != ERROR_SUCCESS
        || f3_broker_sign_sha256_with_cng_key(
            key,
            digest,
            &signature,
            &signature_size
        ) != ERROR_SUCCESS
        || NCryptVerifySignature(
            key,
            &padding,
            (PBYTE)digest,
            sizeof(digest),
            signature,
            signature_size,
            NCRYPT_PAD_PKCS1_FLAG
        ) != ERROR_SUCCESS) {
        goto cleanup;
    }
    status = NCryptExportKey(
        key,
        0,
        BCRYPT_RSAFULLPRIVATE_BLOB,
        NULL,
        NULL,
        0,
        &ignored,
        0
    );
    if (status == ERROR_SUCCESS) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (signature != NULL) {
        SecureZeroMemory(signature, signature_size);
        HeapFree(GetProcessHeap(), 0, signature);
    }
    if (key != 0) {
        deleted = NCryptDeleteKey(key, 0) == ERROR_SUCCESS;
        key = 0;
    }
    if (provider != 0) {
        NCryptFreeObject(provider);
    }
    return ok && deleted && cng_key_absent(key_name);
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

static wchar_t ascii_upper(wchar_t value) {
    if (value >= L'a' && value <= L'z') {
        return value - (L'a' - L'A');
    }
    return value;
}

static int reserved_windows_component(
    const wchar_t *component,
    size_t length
) {
    size_t stem_length = 0;
    wchar_t first;
    wchar_t second;
    wchar_t third;
    while (stem_length < length && component[stem_length] != L'.') {
        ++stem_length;
    }
    if (stem_length < 3 || stem_length > 4) {
        return 0;
    }
    first = ascii_upper(component[0]);
    second = ascii_upper(component[1]);
    third = ascii_upper(component[2]);
    if (stem_length == 3) {
        return (first == L'A' && second == L'U' && third == L'X')
            || (first == L'C' && second == L'O' && third == L'N')
            || (first == L'N' && second == L'U' && third == L'L')
            || (first == L'P' && second == L'R' && third == L'N');
    }
    return ((first == L'C' && second == L'O' && third == L'M')
            || (first == L'L' && second == L'P' && third == L'T'))
        && component[3] >= L'1'
        && component[3] <= L'9';
}

static int strict_windows_candidate_path(const wchar_t *path) {
    size_t length;
    size_t component_start = 3;
    size_t index;
    if (path == NULL || !is_drive_absolute(path) || path[2] != L'\\') {
        return 0;
    }
    length = wcslen(path);
    if (length < 4) {
        return 0;
    }
    for (index = 3; index <= length; ++index) {
        wchar_t value = path[index];
        if (value == L'/'
            || value == L':'
            || value == L'~'
            || (value != L'\0' && (value < 32 || value == 127))) {
            return 0;
        }
        if (value == L'\\' || value == L'\0') {
            size_t component_length = index - component_start;
            if (component_length == 0
                || path[index - 1] == L'.'
                || path[index - 1] == L' '
                || reserved_windows_component(
                    path + component_start,
                    component_length
                )) {
                return 0;
            }
            component_start = index + 1;
        }
    }
    return 1;
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
        && strict_windows_candidate_path(wide)
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
        && F3_BROKER_CNG_PROVIDER[0] != L'\0'
        && F3_BROKER_CNG_KEY_NAME[0] != L'\0'
        && F3_BROKER_SERVICE_NAME[0] != L'\0'
        && F3_BROKER_SERVICE_SID[0] != L'\0'
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

static int append_command_character(
    wchar_t *output,
    size_t capacity,
    size_t *offset,
    wchar_t value
) {
    if (*offset + 1 >= capacity) {
        return 0;
    }
    output[(*offset)++] = value;
    return 1;
}

static int append_quoted_argument(
    const wchar_t *argument,
    wchar_t *output,
    size_t capacity,
    size_t *offset
) {
    const wchar_t *cursor = argument;
    if (argument == NULL
        || !append_command_character(
            output,
            capacity,
            offset,
            L'"'
        )) {
        return 0;
    }
    while (*cursor != L'\0') {
        size_t backslashes = 0;
        size_t index;
        while (*cursor == L'\\') {
            ++backslashes;
            ++cursor;
        }
        if (*cursor == L'\0') {
            backslashes *= 2;
        } else if (*cursor == L'"') {
            backslashes = backslashes * 2 + 1;
        }
        for (index = 0; index < backslashes; ++index) {
            if (!append_command_character(
                    output,
                    capacity,
                    offset,
                    L'\\'
                )) {
                return 0;
            }
        }
        if (*cursor == L'\0') {
            break;
        }
        if (!append_command_character(
                output,
                capacity,
                offset,
                *cursor
            )) {
            return 0;
        }
        ++cursor;
    }
    return append_command_character(
        output,
        capacity,
        offset,
        L'"'
    );
}

static int quoted_command_line(
    const wchar_t *executable,
    const wchar_t *argument,
    wchar_t *output,
    size_t capacity
) {
    size_t offset = 0;
    if (!append_quoted_argument(
            executable,
            output,
            capacity,
            &offset
        )
        || !append_command_character(
            output,
            capacity,
            &offset,
            L' '
        )
        || !append_quoted_argument(
            argument,
            output,
            capacity,
            &offset
        )
        || offset >= capacity) {
        return 0;
    }
    output[offset] = L'\0';
    return 1;
}

static int launch_test_child(
    const wchar_t *output_path,
    int close_job_after_child_ready,
    int close_job_before_child_resume
) {
    STARTUPINFOEXW startup;
    PROCESS_INFORMATION process;
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits;
    PPROC_THREAD_ATTRIBUTE_LIST attributes = NULL;
    SIZE_T attributes_size = 0;
    HANDLE job = NULL;
    wchar_t command_line[32768];
    wchar_t runtime_directory[32768];
    wchar_t *environment = NULL;
    DWORD exit_code = 1;
    BOOL process_in_job = FALSE;
    int ok = 0;
    memset(&startup, 0, sizeof(startup));
    memset(&process, 0, sizeof(process));
    memset(&limits, 0, sizeof(limits));
    startup.StartupInfo.cb = sizeof(startup);
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
        )) {
        goto cleanup;
    }
    InitializeProcThreadAttributeList(NULL, 1, 0, &attributes_size);
    if (attributes_size == 0) {
        goto cleanup;
    }
    attributes = (PPROC_THREAD_ATTRIBUTE_LIST)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        attributes_size
    );
    if (attributes == NULL
        || !InitializeProcThreadAttributeList(
            attributes,
            1,
            0,
            &attributes_size
        )
        || !UpdateProcThreadAttribute(
            attributes,
            0,
            PROC_THREAD_ATTRIBUTE_JOB_LIST,
            &job,
            sizeof(job),
            NULL,
            NULL
        )) {
        goto cleanup;
    }
    startup.lpAttributeList = attributes;
    if (!CreateProcessW(
            F3_BROKER_RUNTIME_PATH,
            command_line,
            NULL,
            NULL,
            FALSE,
            CREATE_SUSPENDED
                | CREATE_UNICODE_ENVIRONMENT
                | CREATE_NO_WINDOW
                | EXTENDED_STARTUPINFO_PRESENT,
            environment,
            runtime_directory,
            &startup.StartupInfo,
            &process
        )
        || !IsProcessInJob(process.hProcess, job, &process_in_job)
        || !process_in_job) {
        if (process.hProcess != NULL) {
            TerminateProcess(process.hProcess, 90);
        }
        goto cleanup;
    }
    if (close_job_before_child_resume) {
        CloseHandle(job);
        job = NULL;
        if (WaitForSingleObject(process.hProcess, 5000) != WAIT_OBJECT_0
            || !GetExitCodeProcess(process.hProcess, &exit_code)
            || exit_code == STILL_ACTIVE
            || GetFileAttributesW(output_path) != INVALID_FILE_ATTRIBUTES) {
            TerminateProcess(process.hProcess, 94);
            goto cleanup;
        }
        ok = 1;
        goto cleanup;
    }
    if (ResumeThread(process.hThread) == (DWORD)-1) {
        TerminateProcess(process.hProcess, 90);
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
    if (attributes != NULL) {
        DeleteProcThreadAttributeList(attributes);
        HeapFree(GetProcessHeap(), 0, attributes);
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
    int close_job_after_child_ready,
    int close_job_before_child_resume
) {
    HeldFile runtime = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile source = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile candidate = {INVALID_HANDLE_VALUE, 0, 0, {0}};
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
        || ((action == ACTION_RUN || action == ACTION_RESUME)
            && !open_held_file(
                F3_BROKER_CREDENTIAL_SLOT_PATH,
                F3_MAX_SECRET_SLOT_BYTES,
                &credential
            ))) {
        ok = 0;
        goto cleanup;
    }
    ok = launch_test_child(
        output_path,
        close_job_after_child_ready,
        close_job_before_child_resume
    );
    if (ok) {
        ok = held_unchanged(&candidate, NULL)
            && held_unchanged(&source, F3_BROKER_SOURCE_SHA256)
            && held_unchanged(&runtime, F3_BROKER_RUNTIME_SHA256)
            && (credential.handle == INVALID_HANDLE_VALUE
                || held_unchanged(&credential, NULL));
    }

cleanup:
    close_held(&credential);
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
    if (argc == 2 && wcscmp(argv[1], L"--test-require-service-identity") == 0) {
        if (!f3_broker_current_process_is_expected_service()) {
            fwprintf(stderr, L"native broker service identity rejected\n");
            return 28;
        }
        return 0;
    }
    if (argc == 3 && wcscmp(argv[1], L"--test-protected-namespace") == 0) {
        if (!f3_broker_current_token_cannot_mutate_directory(argv[2])) {
            fwprintf(stderr, L"native broker protected namespace rejected\n");
            return 29;
        }
        return 0;
    }
    if (argc == 3 && wcscmp(argv[1], L"--test-cng-key-absent") == 0) {
        if (!cng_key_absent(argv[2])) {
            fwprintf(stderr, L"native broker disposable CNG key still present\n");
            return 30;
        }
        return 0;
    }
    if (argc == 3 && wcscmp(argv[1], L"--test-cng-disposable") == 0) {
        if (!disposable_cng_sign_test(argv[2])) {
            fwprintf(stderr, L"native broker disposable CNG signing rejected\n");
            return 31;
        }
        fputws(L"CNG_DISPOSABLE_SIGN_OK\n", stdout);
        return fflush(stdout) == 0 ? 0 : 32;
    }
    if (argc == 4 && wcscmp(argv[1], L"--test-quote-command") == 0) {
        wchar_t command_line[32768];
        if (!quoted_command_line(
                argv[2],
                argv[3],
                command_line,
                sizeof(command_line) / sizeof(command_line[0])
            )
            || fputws(command_line, stdout) < 0
            || fflush(stdout) != 0) {
            SecureZeroMemory(command_line, sizeof(command_line));
            fwprintf(stderr, L"native broker command quoting rejected\n");
            return 27;
        }
        SecureZeroMemory(command_line, sizeof(command_line));
        return 0;
    }
    if (
        argc == 3
        && wcscmp(argv[1], L"--test-current-token-readonly-root") == 0
    ) {
        if (!f3_broker_current_token_cannot_mutate_directory(argv[2])) {
            fwprintf(stderr, L"native broker namespace mutable by caller\n");
            return 25;
        }
        return 0;
    }
    if (argc == 4 && wcscmp(argv[1], L"--test-launch") == 0) {
        if (!test_launch(argv[2], argv[3], 0, 0)) {
            fwprintf(stderr, L"native broker test boundary rejected\n");
            return 21;
        }
        return 0;
    }
    if (argc == 4 && wcscmp(argv[1], L"--test-job-kill") == 0) {
        if (!test_launch(argv[2], argv[3], 1, 0)) {
            fwprintf(stderr, L"native broker job boundary rejected\n");
            return 24;
        }
        return 0;
    }
    if (
        argc == 4
        && wcscmp(argv[1], L"--test-job-pre-resume-kill") == 0
    ) {
        if (!test_launch(argv[2], argv[3], 0, 1)) {
            fwprintf(stderr, L"native broker atomic job boundary rejected\n");
            return 26;
        }
        return 0;
    }
#endif
    if (argc == 3 && wcscmp(argv[1], L"--launch") == 0) {
#if F3_BROKER_PRODUCTION_HANDOFF_READY
        if (!f3_broker_current_process_is_expected_service()) {
            fwprintf(stderr, L"native broker service identity rejected\n");
            return 28;
        }
        fwprintf(stderr, L"native broker credential handoff is unimplemented\n");
#else
        fwprintf(stderr, L"native broker production boundary is unprovisioned\n");
#endif
        return 22;
    }
    fwprintf(stderr, L"native broker invocation rejected\n");
    return 23;
}
