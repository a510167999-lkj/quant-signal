#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif

#include <windows.h>
#include <bcrypt.h>
#include <stdio.h>
#include <stdint.h>
#include <wchar.h>

#ifndef F3_HANDOFF_MANIFEST_HEADER
#error F3_HANDOFF_MANIFEST_HEADER is required
#endif
#include F3_HANDOFF_MANIFEST_HEADER

static int digest_matches(
    const unsigned char digest[32],
    const wchar_t *expected
) {
    static const wchar_t digits[] = L"0123456789abcdef";
    wchar_t observed[65];
    size_t index;
    for (index = 0; index < 32; ++index) {
        observed[index * 2] = digits[digest[index] >> 4];
        observed[index * 2 + 1] = digits[digest[index] & 15];
    }
    observed[64] = L'\0';
    return wcscmp(observed, expected) == 0;
}

static int hash_bytes(
    const unsigned char *data,
    DWORD size,
    unsigned char digest[32]
) {
    BCRYPT_ALG_HANDLE algorithm = NULL;
    BCRYPT_HASH_HANDLE hash = NULL;
    NTSTATUS status;
    int ok = 0;
    status = BCryptOpenAlgorithmProvider(
        &algorithm,
        BCRYPT_SHA256_ALGORITHM,
        NULL,
        0
    );
    if (status < 0) {
        goto cleanup;
    }
    status = BCryptCreateHash(
        algorithm,
        &hash,
        NULL,
        0,
        NULL,
        0,
        0
    );
    if (status < 0
        || BCryptHashData(hash, (PUCHAR)data, size, 0) < 0
        || BCryptFinishHash(hash, digest, 32, 0) < 0) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (hash != NULL) {
        BCryptDestroyHash(hash);
    }
    if (algorithm != NULL) {
        BCryptCloseAlgorithmProvider(algorithm, 0);
    }
    return ok;
}

static int secret_open_denied(void) {
    HANDLE handle = CreateFileW(
        F3_HANDOFF_SECRET_PATH,
        GENERIC_READ,
        FILE_SHARE_READ,
        NULL,
        OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT,
        NULL
    );
    if (handle != INVALID_HANDLE_VALUE) {
        CloseHandle(handle);
        return 0;
    }
    return GetLastError() == ERROR_ACCESS_DENIED;
}

static int completed_write_denied(void) {
    HANDLE handle = CreateFileW(
        F3_HANDOFF_COMPLETED_PATH,
        GENERIC_WRITE,
        0,
        NULL,
        CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL,
        NULL
    );
    if (handle != INVALID_HANDLE_VALUE) {
        CloseHandle(handle);
        DeleteFileW(F3_HANDOFF_COMPLETED_PATH);
        return 0;
    }
    return GetLastError() == ERROR_ACCESS_DENIED;
}

static int token_is_restricted(void) {
    return IsTokenRestricted(NULL) != 0;
}

static int write_all(HANDLE handle, const void *data, DWORD size) {
    const unsigned char *cursor = (const unsigned char *)data;
    DWORD offset = 0;
    while (offset < size) {
        DWORD written = 0;
        if (!WriteFile(
                handle,
                cursor + offset,
                size - offset,
                &written,
                NULL
            )
            || written == 0) {
            return 0;
        }
        offset += written;
    }
    return 1;
}

int wmain(int argc, wchar_t **argv) {
    static const char ready[] =
        "READY factor-v3-formal-native-broker-child/v2\n"
        "original_schema=" F3_HANDOFF_ORIGINAL_SCHEMA "\n"
        "original_action=" F3_HANDOFF_ORIGINAL_ACTION "\n"
        "original_envelope_sha256=" F3_HANDOFF_ORIGINAL_ENVELOPE_SHA256 "\n"
        "original_signature_sha256=" F3_HANDOFF_ORIGINAL_SIGNATURE_SHA256 "\n"
        "original_cas_sha256=" F3_HANDOFF_ORIGINAL_CAS_SHA256 "\n"
        "candidate_sha256=" F3_HANDOFF_CANDIDATE_SHA256 "\n"
        "claim_sha256=" F3_HANDOFF_CLAIM_SHA256 "\n";
    static const char provisional[] =
        "schema=factor-v3-formal-native-broker-provisional/v1\n"
        "restricted_token=1\n"
        "pre_claim_secret_open_denied=1\n"
        "credential_handle_read_ok=1\n"
        "post_claim_secret_reopen_denied=1\n"
        "completed_path_write_denied=1\n"
        "status=provisional\n";
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    HANDLE output = GetStdHandle(STD_OUTPUT_HANDLE);
    HANDLE credential = INVALID_HANDLE_VALUE;
    unsigned char secret[64 * 1024];
    unsigned char digest[32];
    char response[128];
    DWORD count = 0;
    DWORD total = 0;
    unsigned long long remote_value = 0;
    FILE *provisional_file = NULL;
    int ok = 0;
    if (argc != 2
        || input == INVALID_HANDLE_VALUE
        || output == INVALID_HANDLE_VALUE
        || !token_is_restricted()
        || !secret_open_denied()
        || !completed_write_denied()
        || !write_all(output, ready, (DWORD)(sizeof(ready) - 1))
        || !FlushFileBuffers(output)
        || !ReadFile(
            input,
            response,
            (DWORD)(sizeof(response) - 1),
            &count,
            NULL
        )
        || count == 0) {
        goto cleanup;
    }
    response[count] = '\0';
    if (sscanf(response, "HANDLE=%llx\n", &remote_value) != 1) {
        goto cleanup;
    }
    credential = (HANDLE)(uintptr_t)remote_value;
    for (;;) {
        if (!ReadFile(
                credential,
                secret + total,
                (DWORD)sizeof(secret) - total,
                &count,
                NULL
            )) {
            goto cleanup;
        }
        if (count == 0) {
            break;
        }
        total += count;
        if (total == sizeof(secret)) {
            goto cleanup;
        }
    }
    if (total == 0
        || !hash_bytes(secret, total, digest)
        || !digest_matches(digest, F3_HANDOFF_EXPECTED_SECRET_SHA256)
        || !secret_open_denied()
        || !completed_write_denied()
        || _wfopen_s(&provisional_file, argv[1], L"wb") != 0
        || provisional_file == NULL
        || fwrite(
            provisional,
            1,
            sizeof(provisional) - 1,
            provisional_file
        ) != sizeof(provisional) - 1
        || fflush(provisional_file) != 0) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (provisional_file != NULL) {
        fclose(provisional_file);
    }
    if (credential != INVALID_HANDLE_VALUE) {
        CloseHandle(credential);
    }
    SecureZeroMemory(secret, sizeof(secret));
    SecureZeroMemory(digest, sizeof(digest));
    SecureZeroMemory(response, sizeof(response));
    return ok ? 0 : 40;
}
