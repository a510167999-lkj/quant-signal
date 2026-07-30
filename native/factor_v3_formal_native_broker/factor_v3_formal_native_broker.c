#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif

#include <windows.h>
#include <aclapi.h>
#include <bcrypt.h>
#include <limits.h>
#include <ncrypt.h>
#include <sddl.h>
#include <stddef.h>
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
#ifndef F3_BROKER_RESTRICTING_SID
#define F3_BROKER_RESTRICTING_SID L""
#endif
#ifndef F3_BROKER_HANDOFF_ORIGINAL_SCHEMA
#define F3_BROKER_HANDOFF_ORIGINAL_SCHEMA ""
#endif
#ifndef F3_BROKER_HANDOFF_ORIGINAL_ACTION
#define F3_BROKER_HANDOFF_ORIGINAL_ACTION ""
#endif
#ifndef F3_BROKER_HANDOFF_ORIGINAL_ENVELOPE_SHA256
#define F3_BROKER_HANDOFF_ORIGINAL_ENVELOPE_SHA256 ""
#endif
#ifndef F3_BROKER_HANDOFF_ORIGINAL_SIGNATURE_SHA256
#define F3_BROKER_HANDOFF_ORIGINAL_SIGNATURE_SHA256 ""
#endif
#ifndef F3_BROKER_HANDOFF_ORIGINAL_CAS_SHA256
#define F3_BROKER_HANDOFF_ORIGINAL_CAS_SHA256 ""
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
#define F3_HANDOFF_CHILD_PROTOCOL "factor-v3-formal-native-broker-child/v2"
#define F3_HANDOFF_CHILD_PROTOCOL_W L"factor-v3-formal-native-broker-child/v2"
#define F3_PROVISIONAL_SCHEMA "factor-v3-formal-native-broker-provisional/v1"
#define F3_COMPLETED_SCHEMA "factor-v3-formal-native-broker-completed/v1"
#define F3_MAX_CANDIDATE_BYTES (64u * 1024u)
#define F3_MAX_MANIFEST_FILE_BYTES (64u * 1024u * 1024u)
#define F3_MAX_SECRET_SLOT_BYTES (64u * 1024u)
#define F3_RSA_BITS 2048u
#define F3_DISPOSABLE_KEY_PREFIX L"quant-signal-lkj-disposable-test-"
#define F3_MAX_HELD_DIRECTORIES 128u

typedef struct HeldFile {
    HANDLE handle;
    DWORD size_low;
    DWORD size_high;
    wchar_t final_path[32768];
} HeldFile;

typedef struct HeldDirectory {
    HANDLE handle;
    DWORD volume_serial;
    DWORD file_index_high;
    DWORD file_index_low;
} HeldDirectory;

typedef struct HeldDirectoryChain {
    HeldDirectory entries[F3_MAX_HELD_DIRECTORIES];
    size_t count;
} HeldDirectoryChain;

static int strict_windows_candidate_path(const wchar_t *path);
static int parent_directory(
    const wchar_t *path,
    wchar_t *output,
    size_t capacity
);

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

static int unsafe_ordinary_writer_sid(PSID sid) {
    return sid != NULL
        && IsValidSid(sid)
        && (
            sid_matches_well_known(sid, WinWorldSid)
            || sid_matches_well_known(sid, WinAuthenticatedUserSid)
            || sid_matches_well_known(sid, WinBuiltinUsersSid)
            || sid_matches_well_known(sid, WinInteractiveSid)
        );
}

static PSID allowed_ace_sid(void *raw_ace, BYTE ace_type) {
    if (ace_type == ACCESS_ALLOWED_ACE_TYPE) {
        ACCESS_ALLOWED_ACE *ace = (ACCESS_ALLOWED_ACE *)raw_ace;
        return (PSID)&ace->SidStart;
    }
    if (ace_type == ACCESS_ALLOWED_CALLBACK_ACE_TYPE) {
        ACCESS_ALLOWED_CALLBACK_ACE *ace =
            (ACCESS_ALLOWED_CALLBACK_ACE *)raw_ace;
        return (PSID)&ace->SidStart;
    }
    if (ace_type == ACCESS_ALLOWED_OBJECT_ACE_TYPE
        || ace_type == ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE) {
        ACCESS_ALLOWED_OBJECT_ACE *ace =
            (ACCESS_ALLOWED_OBJECT_ACE *)raw_ace;
        size_t offset = offsetof(ACCESS_ALLOWED_OBJECT_ACE, ObjectType);
        if ((ace->Flags & ACE_OBJECT_TYPE_PRESENT) != 0) {
            offset += sizeof(GUID);
        }
        if ((ace->Flags & ACE_INHERITED_OBJECT_TYPE_PRESENT) != 0) {
            offset += sizeof(GUID);
        }
        return (PSID)((BYTE *)raw_ace + offset);
    }
    return NULL;
}

static DWORD allowed_ace_mask(void *raw_ace, BYTE ace_type) {
    if (ace_type == ACCESS_ALLOWED_ACE_TYPE) {
        return ((ACCESS_ALLOWED_ACE *)raw_ace)->Mask;
    }
    if (ace_type == ACCESS_ALLOWED_CALLBACK_ACE_TYPE) {
        return ((ACCESS_ALLOWED_CALLBACK_ACE *)raw_ace)->Mask;
    }
    if (ace_type == ACCESS_ALLOWED_OBJECT_ACE_TYPE
        || ace_type == ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE) {
        return ((ACCESS_ALLOWED_OBJECT_ACE *)raw_ace)->Mask;
    }
    return 0;
}

static int dacl_has_no_unsafe_writer(
    PACL dacl,
    DWORD mutation_rights,
    GENERIC_MAPPING *mapping
) {
    ACL_SIZE_INFORMATION information;
    DWORD index;
    if (dacl == NULL
        || mapping == NULL
        || !GetAclInformation(
            dacl,
            &information,
            sizeof(information),
            AclSizeInformation
        )) {
        return 0;
    }
    for (index = 0; index < information.AceCount; ++index) {
        void *raw_ace = NULL;
        ACE_HEADER *header;
        PSID sid;
        DWORD mask;
        if (!GetAce(dacl, index, &raw_ace) || raw_ace == NULL) {
            return 0;
        }
        header = (ACE_HEADER *)raw_ace;
        if ((header->AceFlags & INHERIT_ONLY_ACE) != 0) {
            continue;
        }
        sid = allowed_ace_sid(raw_ace, header->AceType);
        if (sid == NULL || !unsafe_ordinary_writer_sid(sid)) {
            continue;
        }
        mask = allowed_ace_mask(raw_ace, header->AceType);
        MapGenericMask(&mask, mapping);
        if ((mask & mutation_rights) != 0) {
            return 0;
        }
    }
    return 1;
}

static int held_directory_security_is_immutable(
    HANDLE directory,
    DWORD mutation_rights,
    int reject_unsafe_writer
) {
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
    HANDLE primary_token = NULL;
    HANDLE impersonation_token = NULL;
    DWORD privilege_size = sizeof(privilege_buffer);
    DWORD granted = 0;
    DWORD desired = MAXIMUM_ALLOWED;
    BOOL access_status = FALSE;
    int immutable = 0;
    if (directory == INVALID_HANDLE_VALUE
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
        || (reject_unsafe_writer
            && !dacl_has_no_unsafe_writer(
                dacl,
                mutation_rights,
                &mapping
            ))
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
    SecureZeroMemory(privilege_buffer, sizeof(privilege_buffer));
    return immutable;
}

static void close_held_directory_chain(HeldDirectoryChain *chain) {
    size_t index;
    if (chain == NULL) {
        return;
    }
    for (index = 0; index < chain->count; ++index) {
        if (chain->entries[index].handle != INVALID_HANDLE_VALUE) {
            CloseHandle(chain->entries[index].handle);
            chain->entries[index].handle = INVALID_HANDLE_VALUE;
        }
    }
    chain->count = 0;
}

static void initialize_held_directory_chain(HeldDirectoryChain *chain) {
    size_t index;
    if (chain == NULL) {
        return;
    }
    memset(chain, 0, sizeof(*chain));
    for (index = 0; index < F3_MAX_HELD_DIRECTORIES; ++index) {
        chain->entries[index].handle = INVALID_HANDLE_VALUE;
    }
}

static int append_held_directory(
    const wchar_t *path,
    int final_namespace,
    HeldDirectoryChain *chain
) {
    static const DWORD full_mutation_rights =
        FILE_ADD_FILE
        | FILE_ADD_SUBDIRECTORY
        | FILE_DELETE_CHILD
        | FILE_WRITE_EA
        | FILE_WRITE_ATTRIBUTES
        | DELETE
        | WRITE_DAC
        | WRITE_OWNER;
    static const DWORD ancestor_mutation_rights =
        FILE_DELETE_CHILD
        | DELETE
        | WRITE_DAC
        | WRITE_OWNER;
    BY_HANDLE_FILE_INFORMATION information;
    FILE_ATTRIBUTE_TAG_INFO tag;
    HeldDirectory *held;
    if (chain == NULL || chain->count >= F3_MAX_HELD_DIRECTORIES) {
        return 0;
    }
    held = &chain->entries[chain->count];
    memset(held, 0, sizeof(*held));
    held->handle = CreateFileW(
        path,
        READ_CONTROL | FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ,
        NULL,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
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
        || (information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0
        || (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
        SetLastError(2001);
        CloseHandle(held->handle);
        held->handle = INVALID_HANDLE_VALUE;
        return 0;
    }
    if (!held_directory_security_is_immutable(
            held->handle,
            final_namespace
                ? full_mutation_rights
                : ancestor_mutation_rights,
            final_namespace
        )) {
        SetLastError(final_namespace ? 2003 : 2002);
        CloseHandle(held->handle);
        held->handle = INVALID_HANDLE_VALUE;
        return 0;
    }
    held->volume_serial = information.dwVolumeSerialNumber;
    held->file_index_high = information.nFileIndexHigh;
    held->file_index_low = information.nFileIndexLow;
    chain->count += 1;
    return 1;
}

static int held_directory_chain_unchanged(
    const HeldDirectoryChain *chain
) {
    size_t index;
    if (chain == NULL || chain->count == 0) {
        return 0;
    }
    for (index = 0; index < chain->count; ++index) {
        BY_HANDLE_FILE_INFORMATION information;
        FILE_ATTRIBUTE_TAG_INFO tag;
        const HeldDirectory *held = &chain->entries[index];
        if (held->handle == INVALID_HANDLE_VALUE
            || !GetFileInformationByHandle(
                held->handle,
                &information
            )
            || !GetFileInformationByHandleEx(
                held->handle,
                FileAttributeTagInfo,
                &tag,
                sizeof(tag)
            )
            || information.dwVolumeSerialNumber != held->volume_serial
            || information.nFileIndexHigh != held->file_index_high
            || information.nFileIndexLow != held->file_index_low
            || (information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0
            || (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
            return 0;
        }
    }
    return 1;
}

static int hold_protected_file_chain(
    const wchar_t *file_path,
    HeldDirectoryChain *chain
) {
    wchar_t parent[32768];
    wchar_t prefix[32768];
    size_t length;
    size_t index;
    if (chain == NULL
        || !strict_windows_candidate_path(file_path)
        || !reject_reparse_chain(file_path)
        || !parent_directory(
            file_path,
            parent,
            sizeof(parent) / sizeof(parent[0])
        )) {
        return 0;
    }
    initialize_held_directory_chain(chain);
    length = wcslen(parent);
    if (length < 3) {
        return 0;
    }
    prefix[0] = parent[0];
    prefix[1] = L':';
    prefix[2] = L'\\';
    prefix[3] = L'\0';
    if (!append_held_directory(prefix, length == 3, chain)) {
        goto rejected;
    }
    for (index = 3; index <= length; ++index) {
        if (index != length && parent[index] != L'\\') {
            continue;
        }
        if (index == 3) {
            continue;
        }
        if (index + 1 > sizeof(prefix) / sizeof(prefix[0])) {
            goto rejected;
        }
        memcpy(prefix, parent, index * sizeof(wchar_t));
        prefix[index] = L'\0';
        if (!append_held_directory(
                prefix,
                index == length,
                chain
            )) {
            goto rejected;
        }
    }
    SecureZeroMemory(parent, sizeof(parent));
    SecureZeroMemory(prefix, sizeof(prefix));
    return held_directory_chain_unchanged(chain);

rejected:
    close_held_directory_chain(chain);
    SecureZeroMemory(parent, sizeof(parent));
    SecureZeroMemory(prefix, sizeof(prefix));
    return 0;
}

int hold_production_namespace_chains(
    HeldDirectoryChain *runtime_chain,
    HeldDirectoryChain *source_chain,
    HeldDirectoryChain *credential_chain
) {
    initialize_held_directory_chain(runtime_chain);
    initialize_held_directory_chain(source_chain);
    initialize_held_directory_chain(credential_chain);
    if (runtime_chain == NULL
        || source_chain == NULL
        || credential_chain == NULL
        || !hold_protected_file_chain(
            F3_BROKER_RUNTIME_PATH,
            runtime_chain
        )
        || !hold_protected_file_chain(
            F3_BROKER_SOURCE_PATH,
            source_chain
        )
        || !hold_protected_file_chain(
            F3_BROKER_CREDENTIAL_SLOT_PATH,
            credential_chain
        )) {
        close_held_directory_chain(credential_chain);
        close_held_directory_chain(source_chain);
        close_held_directory_chain(runtime_chain);
        return 0;
    }
    return held_directory_chain_unchanged(runtime_chain)
        && held_directory_chain_unchanged(source_chain)
        && held_directory_chain_unchanged(credential_chain);
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

#ifdef F3_BROKER_TESTING
static int f3_test_handoff_stage = 0;
static DWORD f3_test_handoff_error = ERROR_SUCCESS;

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
        && (
            (component[3] >= L'1' && component[3] <= L'9')
            || component[3] == 0x00b9
            || component[3] == 0x00b2
            || component[3] == 0x00b3
        );
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
            || value == L'*'
            || value == L'?'
            || value == L'"'
            || value == L'<'
            || value == L'>'
            || value == L'|'
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

static wchar_t *sanitized_environment(const wchar_t *protocol) {
    const size_t capacity = 32768;
    wchar_t *block = (wchar_t *)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        capacity * sizeof(wchar_t)
    );
    size_t offset = 0;
    wchar_t windows_directory[32768];
    UINT windows_directory_length;
    if (block == NULL || protocol == NULL || protocol[0] == L'\0') {
        return NULL;
    }
    if (!append_environment_entry(
            block,
            capacity,
            &offset,
            L"FACTOR_V3_FORMAL_NATIVE_BROKER_PROTOCOL",
            protocol
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
    int attributes_initialized = 0;
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
    environment = sanitized_environment(F3_CHILD_PROTOCOL);
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
        )) {
        goto cleanup;
    }
    attributes_initialized = 1;
    if (!UpdateProcThreadAttribute(
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
    if (attributes_initialized) {
        DeleteProcThreadAttributeList(attributes);
    }
    if (attributes != NULL) {
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

static int attribute_init_failure_cleanup_test(void) {
    SIZE_T attributes_size = 1;
    PPROC_THREAD_ATTRIBUTE_LIST attributes =
        (PPROC_THREAD_ATTRIBUTE_LIST)HeapAlloc(
            GetProcessHeap(),
            HEAP_ZERO_MEMORY,
            attributes_size
        );
    int attributes_initialized = 0;
    int expected_failure = 0;
    if (attributes == NULL) {
        return 0;
    }
    if (InitializeProcThreadAttributeList(
            attributes,
            2,
            0,
            &attributes_size
        )) {
        attributes_initialized = 1;
    } else {
        expected_failure = GetLastError() == ERROR_INSUFFICIENT_BUFFER;
    }
    if (attributes_initialized) {
        DeleteProcThreadAttributeList(attributes);
    }
    HeapFree(GetProcessHeap(), 0, attributes);
    return expected_failure && !attributes_initialized;
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
static int test_protected_file_chain(const wchar_t *path) {
    HeldDirectoryChain chain;
    HANDLE file = INVALID_HANDLE_VALUE;
    FILE_ATTRIBUTE_TAG_INFO tag;
    int ok = hold_protected_file_chain(path, &chain)
        && (file = CreateFileW(
            path,
            FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ,
            NULL,
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT,
            NULL
        )) != INVALID_HANDLE_VALUE
        && GetFileInformationByHandleEx(
            file,
            FileAttributeTagInfo,
            &tag,
            sizeof(tag)
        )
        && (tag.FileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0
        && (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0
        && held_directory_chain_unchanged(&chain);
    if (file != INVALID_HANDLE_VALUE) {
        CloseHandle(file);
    }
    close_held_directory_chain(&chain);
    return ok;
}

static int hash_memory(
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

static void digest_to_ascii(
    const unsigned char digest[32],
    char output[65]
) {
    static const char digits[] = "0123456789abcdef";
    size_t index;
    for (index = 0; index < 32; ++index) {
        output[index * 2] = digits[digest[index] >> 4];
        output[index * 2 + 1] = digits[digest[index] & 15];
    }
    output[64] = '\0';
}

static int exact_sha256_ascii(const char *value) {
    size_t index;
    if (value == NULL || strlen(value) != 64) {
        return 0;
    }
    for (index = 0; index < 64; ++index) {
        if (!((value[index] >= '0' && value[index] <= '9')
                || (value[index] >= 'a' && value[index] <= 'f'))) {
            return 0;
        }
    }
    return 1;
}

static int validate_bound_ready(
    const unsigned char *ready,
    DWORD ready_size,
    const char candidate_sha256[65],
    char claim_sha256[65]
) {
    unsigned char claim_digest[32];
    char claim_fields[1024];
    char expected[1400];
    int claim_length;
    int expected_length;
    if (strcmp(
            F3_BROKER_HANDOFF_ORIGINAL_SCHEMA,
            "factor-v3-formal-supervisor-launch-authorization/v2"
        ) != 0
        || strcmp(F3_BROKER_HANDOFF_ORIGINAL_ACTION, "run") != 0
        || !exact_sha256_ascii(
            F3_BROKER_HANDOFF_ORIGINAL_ENVELOPE_SHA256
        )
        || !exact_sha256_ascii(
            F3_BROKER_HANDOFF_ORIGINAL_SIGNATURE_SHA256
        )
        || !exact_sha256_ascii(F3_BROKER_HANDOFF_ORIGINAL_CAS_SHA256)
        || !exact_sha256_ascii(candidate_sha256)) {
        return 0;
    }
    claim_length = snprintf(
        claim_fields,
        sizeof(claim_fields),
        "original_schema=%s\n"
        "original_action=%s\n"
        "original_envelope_sha256=%s\n"
        "original_signature_sha256=%s\n"
        "original_cas_sha256=%s\n"
        "candidate_sha256=%s\n",
        F3_BROKER_HANDOFF_ORIGINAL_SCHEMA,
        F3_BROKER_HANDOFF_ORIGINAL_ACTION,
        F3_BROKER_HANDOFF_ORIGINAL_ENVELOPE_SHA256,
        F3_BROKER_HANDOFF_ORIGINAL_SIGNATURE_SHA256,
        F3_BROKER_HANDOFF_ORIGINAL_CAS_SHA256,
        candidate_sha256
    );
    if (claim_length <= 0
        || (size_t)claim_length >= sizeof(claim_fields)
        || !hash_memory(
            (const unsigned char *)claim_fields,
            (DWORD)claim_length,
            claim_digest
        )) {
        SecureZeroMemory(claim_fields, sizeof(claim_fields));
        return 0;
    }
    digest_to_ascii(claim_digest, claim_sha256);
    expected_length = snprintf(
        expected,
        sizeof(expected),
        "READY " F3_HANDOFF_CHILD_PROTOCOL "\n"
        "%s"
        "claim_sha256=%s\n",
        claim_fields,
        claim_sha256
    );
    SecureZeroMemory(claim_digest, sizeof(claim_digest));
    SecureZeroMemory(claim_fields, sizeof(claim_fields));
    if (expected_length <= 0
        || (size_t)expected_length >= sizeof(expected)
        || ready_size != (DWORD)expected_length
        || memcmp(ready, expected, ready_size) != 0) {
        SecureZeroMemory(expected, sizeof(expected));
        SecureZeroMemory(claim_sha256, 65);
        return 0;
    }
    SecureZeroMemory(expected, sizeof(expected));
    return 1;
}

static int read_bound_ready(
    HANDLE pipe,
    const char candidate_sha256[65],
    char claim_sha256[65]
) {
    unsigned char buffer[1400];
    DWORD total = 0;
    DWORD available = 0;
    DWORD count = 0;
    DWORD elapsed = 0;
    int ok = 0;
    while (elapsed < 10000 && total < sizeof(buffer)) {
        if (!PeekNamedPipe(pipe, NULL, 0, NULL, &available, NULL)) {
            goto cleanup;
        }
        if (available == 0) {
            Sleep(10);
            elapsed += 10;
            continue;
        }
        if (available > sizeof(buffer) - total
            || !ReadFile(
                pipe,
                buffer + total,
                available,
                &count,
                NULL
            )
            || count == 0) {
            goto cleanup;
        }
        total += count;
        if (total > 0 && buffer[total - 1] == '\n') {
            char marker[] = "claim_sha256=";
            size_t minimum = strlen(marker) + 65;
            if (total >= minimum
                && memcmp(
                    buffer + total - minimum,
                    marker,
                    strlen(marker)
                ) == 0) {
                break;
            }
        }
    }
    ok = validate_bound_ready(
        buffer,
        total,
        candidate_sha256,
        claim_sha256
    );

cleanup:
    SecureZeroMemory(buffer, sizeof(buffer));
    return ok;
}

static int create_restricted_primary_token(HANDLE *restricted_token) {
    HANDLE primary = NULL;
    PSID disabled_sid = NULL;
    SID_AND_ATTRIBUTES disabled;
    int ok = 0;
    memset(&disabled, 0, sizeof(disabled));
    if (restricted_token == NULL
        || F3_BROKER_RESTRICTING_SID[0] == L'\0') {
        return 0;
    }
    *restricted_token = NULL;
    if (!OpenProcessToken(
            GetCurrentProcess(),
            TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ASSIGN_PRIMARY,
            &primary
        )
        || !ConvertStringSidToSidW(
            F3_BROKER_RESTRICTING_SID,
            &disabled_sid
        )) {
        goto cleanup;
    }
    disabled.Sid = disabled_sid;
    disabled.Attributes = 0;
    if (!CreateRestrictedToken(
            primary,
            DISABLE_MAX_PRIVILEGE,
            1,
            &disabled,
            0,
            NULL,
            0,
            NULL,
            restricted_token
        )) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (!ok && restricted_token != NULL && *restricted_token != NULL) {
        CloseHandle(*restricted_token);
        *restricted_token = NULL;
    }
    if (primary != NULL) {
        CloseHandle(primary);
    }
    if (disabled_sid != NULL) {
        LocalFree(disabled_sid);
    }
    return ok;
}

static int read_exact_provisional(
    const wchar_t *path,
    unsigned char digest[32]
) {
    static const unsigned char expected[] =
        "schema=" F3_PROVISIONAL_SCHEMA "\n"
        "restricted_token=1\n"
        "pre_claim_secret_open_denied=1\n"
        "credential_handle_read_ok=1\n"
        "post_claim_secret_reopen_denied=1\n"
        "runtime_namespace_write_denied=1\n"
        "completed_path_write_denied=1\n"
        "status=provisional\n";
    HeldFile provisional = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    unsigned char *raw = NULL;
    DWORD raw_size = 0;
    int ok = open_held_file(
            path,
            sizeof(expected) - 1,
            &provisional
        )
        && read_candidate(&provisional, &raw, &raw_size)
        && raw_size == sizeof(expected) - 1
        && memcmp(raw, expected, raw_size) == 0
        && hash_memory(raw, raw_size, digest)
        && held_unchanged(&provisional, NULL);
    if (raw != NULL) {
        SecureZeroMemory(raw, (SIZE_T)raw_size + 1);
        HeapFree(GetProcessHeap(), 0, raw);
    }
    close_held(&provisional);
    return ok;
}

static int atomic_write_new(
    const wchar_t *path,
    const unsigned char *data,
    DWORD size
) {
    wchar_t temporary[32768];
    HANDLE handle = INVALID_HANDLE_VALUE;
    DWORD written = 0;
    DWORD total = 0;
    int length;
    int ok = 0;
    if (!strict_windows_candidate_path(path)
        || !reject_reparse_chain(path)
        || GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES) {
        return 0;
    }
    length = swprintf(
        temporary,
        sizeof(temporary) / sizeof(temporary[0]),
        L"%ls.tmp-%lu-%llu",
        path,
        GetCurrentProcessId(),
        (unsigned long long)GetTickCount64()
    );
    if (length <= 0
        || (size_t)length >= sizeof(temporary) / sizeof(temporary[0])) {
        return 0;
    }
    handle = CreateFileW(
        temporary,
        GENERIC_WRITE,
        0,
        NULL,
        CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH,
        NULL
    );
    if (handle == INVALID_HANDLE_VALUE) {
        goto cleanup;
    }
    while (total < size) {
        if (!WriteFile(
                handle,
                data + total,
                size - total,
                &written,
                NULL
            )
            || written == 0) {
            goto cleanup;
        }
        total += written;
    }
    if (!FlushFileBuffers(handle)) {
        goto cleanup;
    }
    CloseHandle(handle);
    handle = INVALID_HANDLE_VALUE;
    if (!MoveFileExW(temporary, path, MOVEFILE_WRITE_THROUGH)) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (handle != INVALID_HANDLE_VALUE) {
        CloseHandle(handle);
    }
    if (!ok) {
        DeleteFileW(temporary);
    }
    SecureZeroMemory(temporary, sizeof(temporary));
    return ok;
}

static int create_disposable_cng_key(
    const wchar_t *key_name,
    NCRYPT_PROV_HANDLE *provider,
    NCRYPT_KEY_HANDLE *key
) {
    DWORD bits = F3_RSA_BITS;
    DWORD export_policy = 0;
    if (key_name == NULL
        || provider == NULL
        || key == NULL
        || wcsncmp(
            key_name,
            F3_DISPOSABLE_KEY_PREFIX,
            wcslen(F3_DISPOSABLE_KEY_PREFIX)
        ) != 0
        || !cng_key_absent(key_name)
        || f3_broker_open_cng_provider(provider) != ERROR_SUCCESS
        || NCryptCreatePersistedKey(
            *provider,
            key,
            F3_BROKER_CNG_ALGORITHM,
            key_name,
            0,
            0
        ) != ERROR_SUCCESS
        || NCryptSetProperty(
            *key,
            NCRYPT_LENGTH_PROPERTY,
            (PBYTE)&bits,
            sizeof(bits),
            0
        ) != ERROR_SUCCESS
        || NCryptSetProperty(
            *key,
            NCRYPT_EXPORT_POLICY_PROPERTY,
            (PBYTE)&export_policy,
            sizeof(export_policy),
            0
        ) != ERROR_SUCCESS
        || NCryptFinalizeKey(*key, 0) != ERROR_SUCCESS) {
        return 0;
    }
    return 1;
}

static int write_signed_completed(
    const wchar_t *completed_path,
    const wchar_t *key_name,
    const char claim_sha256[65],
    const char candidate_sha256[65],
    const unsigned char provisional_digest[32]
) {
    BCRYPT_PKCS1_PADDING_INFO padding = {BCRYPT_SHA256_ALGORITHM};
    NCRYPT_PROV_HANDLE provider = 0;
    NCRYPT_KEY_HANDLE key = 0;
    unsigned char unsigned_digest[32];
    unsigned char *signature = NULL;
    DWORD signature_size = 0;
    DWORD private_size = 0;
    char provisional_sha256[65];
    char unsigned_ledger[1024];
    unsigned char *ledger = NULL;
    size_t ledger_capacity;
    int unsigned_length;
    int signature_verified = 0;
    int key_deleted = 0;
    int ok = 0;
    size_t index;
    size_t offset;
    digest_to_ascii(provisional_digest, provisional_sha256);
    unsigned_length = snprintf(
        unsigned_ledger,
        sizeof(unsigned_ledger),
        "schema=" F3_COMPLETED_SCHEMA "\n"
        "status=completed\n"
        "claim_sha256=%s\n"
        "candidate_sha256=%s\n"
        "provisional_sha256=%s\n"
        "signature_algorithm=RSA-PKCS1-SHA256\n"
        "signature_verified_before_key_delete=1\n",
        claim_sha256,
        candidate_sha256,
        provisional_sha256
    );
    if (unsigned_length <= 0
        || (size_t)unsigned_length >= sizeof(unsigned_ledger)
        || !hash_memory(
            (const unsigned char *)unsigned_ledger,
            (DWORD)unsigned_length,
            unsigned_digest
        )
        || !create_disposable_cng_key(key_name, &provider, &key)
        || f3_broker_sign_sha256_with_cng_key(
            key,
            unsigned_digest,
            &signature,
            &signature_size
        ) != ERROR_SUCCESS
        || NCryptVerifySignature(
            key,
            &padding,
            unsigned_digest,
            sizeof(unsigned_digest),
            signature,
            signature_size,
            NCRYPT_PAD_PKCS1_FLAG
        ) != ERROR_SUCCESS
        || NCryptExportKey(
            key,
            0,
            BCRYPT_RSAFULLPRIVATE_BLOB,
            NULL,
            NULL,
            0,
            &private_size,
            0
        ) == ERROR_SUCCESS) {
        goto cleanup;
    }
    signature_verified = 1;
    ledger_capacity = (size_t)unsigned_length
        + strlen("signature_hex=\n")
        + (size_t)signature_size * 2
        + 1;
    ledger = (unsigned char *)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        ledger_capacity
    );
    if (ledger == NULL) {
        goto cleanup;
    }
    memcpy(ledger, unsigned_ledger, (size_t)unsigned_length);
    offset = (size_t)unsigned_length;
    memcpy(ledger + offset, "signature_hex=", strlen("signature_hex="));
    offset += strlen("signature_hex=");
    for (index = 0; index < signature_size; ++index) {
        static const char digits[] = "0123456789abcdef";
        ledger[offset++] = (unsigned char)digits[signature[index] >> 4];
        ledger[offset++] = (unsigned char)digits[signature[index] & 15];
    }
    ledger[offset++] = '\n';
    if (offset + 1 > ledger_capacity) {
        goto cleanup;
    }
    key_deleted = NCryptDeleteKey(key, 0) == ERROR_SUCCESS;
    key = 0;
    if (!key_deleted
        || !cng_key_absent(key_name)
        || !atomic_write_new(
            completed_path,
            ledger,
            (DWORD)offset
        )) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (signature != NULL) {
        SecureZeroMemory(signature, signature_size);
        HeapFree(GetProcessHeap(), 0, signature);
    }
    if (key != 0) {
        key_deleted = NCryptDeleteKey(key, 0) == ERROR_SUCCESS;
        key = 0;
    }
    if (provider != 0) {
        NCryptFreeObject(provider);
    }
    if (ledger != NULL) {
        SecureZeroMemory(ledger, ledger_capacity);
        HeapFree(GetProcessHeap(), 0, ledger);
    }
    SecureZeroMemory(unsigned_digest, sizeof(unsigned_digest));
    SecureZeroMemory(unsigned_ledger, sizeof(unsigned_ledger));
    SecureZeroMemory(provisional_sha256, sizeof(provisional_sha256));
    return ok
        && signature_verified
        && key_deleted
        && cng_key_absent(key_name);
}

static int launch_restricted_handoff_child(
    const wchar_t *provisional_path,
    const char candidate_sha256[65],
    const wchar_t *completed_path,
    const wchar_t *key_name,
    HeldFile *runtime,
    HeldFile *source,
    HeldFile *candidate
) {
    STARTUPINFOEXW startup;
    PROCESS_INFORMATION process;
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits;
    SECURITY_ATTRIBUTES pipe_security;
    PPROC_THREAD_ATTRIBUTE_LIST attributes = NULL;
    SIZE_T attributes_size = 0;
    HANDLE inherited_handles[3];
    HANDLE job = NULL;
    HANDLE restricted_token = NULL;
    HANDLE child_input = INVALID_HANDLE_VALUE;
    HANDLE parent_input = INVALID_HANDLE_VALUE;
    HANDLE parent_output = INVALID_HANDLE_VALUE;
    HANDLE child_output = INVALID_HANDLE_VALUE;
    HANDLE null_error = INVALID_HANDLE_VALUE;
    HANDLE remote_credential = NULL;
    HeldFile credential = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    wchar_t command_line[32768];
    wchar_t runtime_directory[32768];
    wchar_t *environment = NULL;
    char claim_sha256[65];
    char response[128];
    char candidate_hash[65];
    unsigned char candidate_digest[32];
    unsigned char provisional_digest[32];
    DWORD response_size;
    DWORD written = 0;
    DWORD child_exit = 1;
    BOOL process_in_job = FALSE;
    int attributes_initialized = 0;
    int child_created = 0;
    int ok = 0;
    memset(&startup, 0, sizeof(startup));
    memset(&process, 0, sizeof(process));
    memset(&limits, 0, sizeof(limits));
    memset(&pipe_security, 0, sizeof(pipe_security));
    memset(claim_sha256, 0, sizeof(claim_sha256));
    memset(candidate_hash, 0, sizeof(candidate_hash));
    pipe_security.nLength = sizeof(pipe_security);
    pipe_security.bInheritHandle = TRUE;
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    f3_test_handoff_stage = 10;
    if (!hash_held_file(candidate, candidate_digest)) {
        goto cleanup;
    }
    digest_to_ascii(candidate_digest, candidate_hash);
    if (strcmp(candidate_hash, candidate_sha256) != 0
        || !quoted_command_line(
            F3_BROKER_RUNTIME_PATH,
            provisional_path,
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
    f3_test_handoff_stage = 121;
    environment = sanitized_environment(F3_HANDOFF_CHILD_PROTOCOL_W);
    if (environment == NULL) {
        goto cleanup;
    }
    f3_test_handoff_stage = 122;
    if (!create_restricted_primary_token(&restricted_token)) {
        goto cleanup;
    }
    f3_test_handoff_stage = 123;
    if (!CreatePipe(
            &child_input,
            &parent_input,
            &pipe_security,
            0
        )) {
        goto cleanup;
    }
    f3_test_handoff_stage = 124;
    if (!CreatePipe(
            &parent_output,
            &child_output,
            &pipe_security,
            0
        )) {
        goto cleanup;
    }
    f3_test_handoff_stage = 125;
    if (!SetHandleInformation(
            parent_input,
            HANDLE_FLAG_INHERIT,
            0
        )
        || !SetHandleInformation(
            parent_output,
            HANDLE_FLAG_INHERIT,
            0
        )) {
        goto cleanup;
    }
    f3_test_handoff_stage = 13;
    null_error = CreateFileW(
        L"NUL",
        GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        &pipe_security,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        NULL
    );
    if (null_error == INVALID_HANDLE_VALUE) {
        goto cleanup;
    }
    startup.StartupInfo.hStdInput = child_input;
    startup.StartupInfo.hStdOutput = child_output;
    startup.StartupInfo.hStdError = null_error;
    inherited_handles[0] = child_input;
    inherited_handles[1] = child_output;
    inherited_handles[2] = null_error;
    f3_test_handoff_stage = 14;
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
    f3_test_handoff_stage = 15;
    InitializeProcThreadAttributeList(NULL, 2, 0, &attributes_size);
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
            2,
            0,
            &attributes_size
        )) {
        goto cleanup;
    }
    attributes_initialized = 1;
    if (!UpdateProcThreadAttribute(
            attributes,
            0,
            PROC_THREAD_ATTRIBUTE_JOB_LIST,
            &job,
            sizeof(job),
            NULL,
            NULL
        )
        || !UpdateProcThreadAttribute(
            attributes,
            0,
            PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            inherited_handles,
            sizeof(inherited_handles),
            NULL,
            NULL
        )) {
        goto cleanup;
    }
    startup.lpAttributeList = attributes;
    f3_test_handoff_stage = 16;
    if (!CreateProcessAsUserW(
            restricted_token,
            F3_BROKER_RUNTIME_PATH,
            command_line,
            NULL,
            NULL,
            TRUE,
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
    child_created = 1;
    CloseHandle(child_input);
    child_input = INVALID_HANDLE_VALUE;
    CloseHandle(child_output);
    child_output = INVALID_HANDLE_VALUE;
    CloseHandle(null_error);
    null_error = INVALID_HANDLE_VALUE;
    f3_test_handoff_stage = 17;
    if (ResumeThread(process.hThread) == (DWORD)-1) {
        goto cleanup;
    }
    if (!read_bound_ready(
            parent_output,
            candidate_hash,
            claim_sha256
        )) {
        DWORD diagnostic_exit = STILL_ACTIVE;
        WaitForSingleObject(process.hProcess, 1000);
        if (GetExitCodeProcess(process.hProcess, &diagnostic_exit)
            && diagnostic_exit != STILL_ACTIVE) {
            f3_test_handoff_error = diagnostic_exit;
        }
        goto cleanup;
    }
    f3_test_handoff_stage = 18;
    if (!open_held_file(
            F3_BROKER_CREDENTIAL_SLOT_PATH,
            F3_MAX_SECRET_SLOT_BYTES,
            &credential
        )
        || !DuplicateHandle(
            GetCurrentProcess(),
            credential.handle,
            process.hProcess,
            &remote_credential,
            GENERIC_READ,
            FALSE,
            0
        )) {
        goto cleanup;
    }
    f3_test_handoff_stage = 19;
    response_size = (DWORD)snprintf(
        response,
        sizeof(response),
        "HANDLE=%llx\n",
        (unsigned long long)(uintptr_t)remote_credential
    );
    if (response_size == 0
        || response_size >= sizeof(response)
        || !WriteFile(
            parent_input,
            response,
            response_size,
            &written,
            NULL
        )
        || written != response_size
        || !FlushFileBuffers(parent_input)) {
        goto cleanup;
    }
    CloseHandle(parent_input);
    parent_input = INVALID_HANDLE_VALUE;
    f3_test_handoff_stage = 20;
    if (WaitForSingleObject(process.hProcess, 30000) != WAIT_OBJECT_0
        || !GetExitCodeProcess(process.hProcess, &child_exit)
        || child_exit != 0
        || !held_unchanged(candidate, NULL)
        || !held_unchanged(source, F3_BROKER_SOURCE_SHA256)
        || !held_unchanged(runtime, F3_BROKER_RUNTIME_SHA256)
        || !held_unchanged(&credential, NULL)
        || !read_exact_provisional(
            provisional_path,
            provisional_digest
        )
        || !write_signed_completed(
            completed_path,
            key_name,
            claim_sha256,
            candidate_hash,
            provisional_digest
        )) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (!ok && f3_test_handoff_error == ERROR_SUCCESS) {
        f3_test_handoff_error = GetLastError();
    }
    if (!ok && child_created && process.hProcess != NULL) {
        TerminateProcess(process.hProcess, 97);
        WaitForSingleObject(process.hProcess, 5000);
    }
    if (process.hThread != NULL) {
        CloseHandle(process.hThread);
    }
    if (process.hProcess != NULL) {
        CloseHandle(process.hProcess);
    }
    close_held(&credential);
    if (child_input != INVALID_HANDLE_VALUE) {
        CloseHandle(child_input);
    }
    if (parent_input != INVALID_HANDLE_VALUE) {
        CloseHandle(parent_input);
    }
    if (parent_output != INVALID_HANDLE_VALUE) {
        CloseHandle(parent_output);
    }
    if (child_output != INVALID_HANDLE_VALUE) {
        CloseHandle(child_output);
    }
    if (null_error != INVALID_HANDLE_VALUE) {
        CloseHandle(null_error);
    }
    if (restricted_token != NULL) {
        CloseHandle(restricted_token);
    }
    if (job != NULL) {
        CloseHandle(job);
    }
    if (attributes_initialized) {
        DeleteProcThreadAttributeList(attributes);
    }
    if (attributes != NULL) {
        HeapFree(GetProcessHeap(), 0, attributes);
    }
    if (environment != NULL) {
        SecureZeroMemory(environment, 32768 * sizeof(wchar_t));
        HeapFree(GetProcessHeap(), 0, environment);
    }
    SecureZeroMemory(command_line, sizeof(command_line));
    SecureZeroMemory(runtime_directory, sizeof(runtime_directory));
    SecureZeroMemory(claim_sha256, sizeof(claim_sha256));
    SecureZeroMemory(candidate_hash, sizeof(candidate_hash));
    SecureZeroMemory(candidate_digest, sizeof(candidate_digest));
    SecureZeroMemory(provisional_digest, sizeof(provisional_digest));
    SecureZeroMemory(response, sizeof(response));
    return ok;
}

static int test_credential_handoff(
    const wchar_t *candidate_path,
    const wchar_t *provisional_path,
    const wchar_t *completed_path,
    const wchar_t *key_name
) {
    HeldFile runtime = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile source = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile candidate = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    CandidateAction action = ACTION_INVALID;
    unsigned char digest[32];
    char candidate_sha256[65];
    int ok = open_and_validate_public_boundary(
        candidate_path,
        &runtime,
        &source,
        &candidate,
        &action
    );
    f3_test_handoff_stage = 1;
    if (!ok
        || action != ACTION_RUN
        || !hash_held_file(&candidate, digest)) {
        ok = 0;
        goto cleanup;
    }
    f3_test_handoff_stage = 2;
    digest_to_ascii(digest, candidate_sha256);
    ok = launch_restricted_handoff_child(
        provisional_path,
        candidate_sha256,
        completed_path,
        key_name,
        &runtime,
        &source,
        &candidate
    );

cleanup:
    SecureZeroMemory(digest, sizeof(digest));
    SecureZeroMemory(candidate_sha256, sizeof(candidate_sha256));
    close_held(&candidate);
    close_held(&source);
    close_held(&runtime);
    return ok;
}

static int test_launch(
    const wchar_t *candidate_path,
    const wchar_t *output_path,
    int close_job_after_child_ready,
    int close_job_before_child_resume
) {
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
    if (!ok) {
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
            && held_unchanged(&runtime, F3_BROKER_RUNTIME_SHA256);
    }

cleanup:
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
        argc == 2
        && wcscmp(
            argv[1],
            L"--test-attribute-init-failure-cleanup"
        ) == 0
    ) {
        if (!attribute_init_failure_cleanup_test()) {
            fwprintf(stderr, L"native broker attribute cleanup rejected\n");
            return 34;
        }
        return 0;
    }
    if (
        argc == 6
        && wcscmp(argv[1], L"--test-credential-handoff") == 0
    ) {
        if (!test_credential_handoff(
                argv[2],
                argv[3],
                argv[4],
                argv[5]
            )) {
            fwprintf(
                stderr,
                L"native broker credential handoff rejected stage=%d error=%lu\n",
                f3_test_handoff_stage,
                f3_test_handoff_error
            );
            return 33;
        }
        return 0;
    }
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
    if (argc == 3 && wcscmp(argv[1], L"--test-protected-file-chain") == 0) {
        if (!test_protected_file_chain(argv[2])) {
            fwprintf(
                stderr,
                L"native broker namespace chain rejected error=%lu\n",
                GetLastError()
            );
            return 35;
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
        HeldDirectoryChain runtime_chain;
        HeldDirectoryChain source_chain;
        HeldDirectoryChain credential_chain;
        if (!f3_broker_current_process_is_expected_service()) {
            fwprintf(stderr, L"native broker service identity rejected\n");
            return 28;
        }
        if (!hold_production_namespace_chains(
                &runtime_chain,
                &source_chain,
                &credential_chain
            )) {
            fwprintf(stderr, L"native broker production namespace rejected\n");
            return 36;
        }
        close_held_directory_chain(&credential_chain);
        close_held_directory_chain(&source_chain);
        close_held_directory_chain(&runtime_chain);
        fwprintf(stderr, L"native broker credential handoff is unimplemented\n");
#else
        fwprintf(stderr, L"native broker production boundary is unprovisioned\n");
#endif
        return 22;
    }
    fwprintf(stderr, L"native broker invocation rejected\n");
    return 23;
}
