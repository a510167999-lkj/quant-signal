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
#ifndef F3_BROKER_WORKER_SID
#define F3_BROKER_WORKER_SID L""
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
#ifndef F3_BROKER_EXECUTION_PUBLIC_MODULUS_HEX
#define F3_BROKER_EXECUTION_PUBLIC_MODULUS_HEX ""
#endif
#ifndef F3_BROKER_EXECUTION_PUBLIC_EXPONENT
#define F3_BROKER_EXECUTION_PUBLIC_EXPONENT 0u
#endif
#ifndef F3_BROKER_COMPLETION_KEY_ID
#define F3_BROKER_COMPLETION_KEY_ID ""
#endif
#ifndef F3_BROKER_COMPLETION_KEY_VERSION
#define F3_BROKER_COMPLETION_KEY_VERSION ""
#endif
#ifndef F3_BROKER_COMPLETION_PUBLIC_BLOB_HEX
#define F3_BROKER_COMPLETION_PUBLIC_BLOB_HEX ""
#endif
#ifndef F3_BROKER_COMPLETION_PUBLIC_BLOB_SHA256
#define F3_BROKER_COMPLETION_PUBLIC_BLOB_SHA256 ""
#endif

#ifdef F3_BROKER_TESTING
#ifndef F3_BROKER_DISPOSABLE_TEST_MANIFEST
#define F3_BROKER_DISPOSABLE_TEST_MANIFEST 0
#endif
#ifndef F3_BROKER_TESTING_SUPERVISOR_TOKEN_COMPATIBILITY
#define F3_BROKER_TESTING_SUPERVISOR_TOKEN_COMPATIBILITY 0
#endif
#if F3_BROKER_DISPOSABLE_TEST_MANIFEST != 1
#error F3_BROKER_TESTING requires a disposable test manifest
#endif
#if F3_BROKER_PRODUCTION_HANDOFF_READY != 0
#error F3_BROKER_TESTING is forbidden in a production-ready build
#endif
#endif

#define F3_CANDIDATE_SCHEMA "factor-v3-formal-native-broker-candidate/v1"
#define F3_CANDIDATE_SCHEMA_V2 "factor-v3-formal-native-broker-candidate/v2"
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
#define F3_MAX_AUTHORIZATION_BYTES (4u * 1024u * 1024u)
#define F3_RSA_BITS 2048u
#define F3_EXECUTION_RSA_BITS 3072u
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

typedef struct ProtectedCompletionNamespace {
    HeldDirectoryChain chain;
    wchar_t final_name[70];
} ProtectedCompletionNamespace;

typedef LONG F3_NTSTATUS;

typedef struct F3_UNICODE_STRING {
    USHORT Length;
    USHORT MaximumLength;
    PWSTR Buffer;
} F3_UNICODE_STRING;

typedef struct F3_OBJECT_ATTRIBUTES {
    ULONG Length;
    HANDLE RootDirectory;
    F3_UNICODE_STRING *ObjectName;
    ULONG Attributes;
    PVOID SecurityDescriptor;
    PVOID SecurityQualityOfService;
} F3_OBJECT_ATTRIBUTES;

typedef struct F3_IO_STATUS_BLOCK {
    union {
        F3_NTSTATUS Status;
        PVOID Pointer;
    };
    ULONG_PTR Information;
} F3_IO_STATUS_BLOCK;

typedef F3_NTSTATUS (NTAPI *F3_NT_CREATE_FILE)(
    PHANDLE,
    ACCESS_MASK,
    F3_OBJECT_ATTRIBUTES *,
    F3_IO_STATUS_BLOCK *,
    PLARGE_INTEGER,
    ULONG,
    ULONG,
    ULONG,
    ULONG,
    PVOID,
    ULONG
);

typedef F3_NTSTATUS (NTAPI *F3_RTL_GET_LAST_NT_STATUS)(void);

typedef F3_NTSTATUS (NTAPI *F3_NT_SET_INFORMATION_FILE)(
    HANDLE,
    F3_IO_STATUS_BLOCK *,
    PVOID,
    ULONG,
    ULONG
);

typedef ULONG (NTAPI *F3_RTL_NT_STATUS_TO_DOS_ERROR)(F3_NTSTATUS);

typedef struct F3_FILE_RENAME_INFORMATION {
    BOOLEAN ReplaceIfExists;
    HANDLE RootDirectory;
    ULONG FileNameLength;
    WCHAR FileName[1];
} F3_FILE_RENAME_INFORMATION;

#define F3_OBJ_CASE_INSENSITIVE 0x00000040UL
#define F3_FILE_OPEN 0x00000001UL
#define F3_FILE_CREATE 0x00000002UL
#define F3_FILE_WRITE_THROUGH 0x00000002UL
#define F3_FILE_SYNCHRONOUS_IO_NONALERT 0x00000020UL
#define F3_FILE_NON_DIRECTORY_FILE 0x00000040UL
#define F3_FILE_OPEN_REPARSE_POINT 0x00200000UL
#define F3_FILE_RENAME_INFORMATION_CLASS 10UL

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

typedef struct ByteSlice {
    const char *value;
    size_t length;
} ByteSlice;

typedef struct ProductionCandidate {
    CandidateAction action;
    ByteSlice authorization_path;
    ByteSlice completion_marker_path;
    ByteSlice publication_receipt_path;
    ByteSlice launch_authorization_path;
    ByteSlice execution_ledger_root;
    ByteSlice resume_authorization_path;
    ByteSlice resume_status_path;
} ProductionCandidate;

typedef struct SignedEnvelope {
    const unsigned char *payload;
    DWORD payload_size;
    unsigned char signature[384];
} SignedEnvelope;

#ifdef F3_BROKER_TESTING
static int f3_test_production_validation_stage = 0;
static int f3_test_production_launch_stage = 0;
static int f3_test_completion_namespace_stage = 0;
static int f3_test_pipe_stage = 0;
static DWORD f3_test_pipe_error = ERROR_SUCCESS;
static DWORD f3_test_atomic_write_error = ERROR_SUCCESS;
static F3_NTSTATUS f3_test_atomic_write_ntstatus = 0;
#define F3_PRODUCTION_LAUNCH_STAGE(value) \
    do { f3_test_production_launch_stage = (value); } while (0)
#define F3_PIPE_STAGE(value) \
    do { \
        f3_test_pipe_stage = (value); \
        f3_test_pipe_error = GetLastError(); \
    } while (0)
#else
#define F3_PRODUCTION_LAUNCH_STAGE(value) \
    do { (void)(value); } while (0)
#define F3_PIPE_STAGE(value) \
    do { (void)(value); } while (0)
#endif

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

#ifdef F3_BROKER_TESTING
static int sid_matches_current_token_user(PSID sid) {
    HANDLE token = NULL;
    TOKEN_USER *user = NULL;
    DWORD required = 0;
    int matches = 0;
    if (sid == NULL
        || !OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
        goto cleanup;
    }
    GetTokenInformation(token, TokenUser, NULL, 0, &required);
    if (GetLastError() != ERROR_INSUFFICIENT_BUFFER || required == 0) {
        goto cleanup;
    }
    user = (TOKEN_USER *)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        required
    );
    if (user == NULL
        || !GetTokenInformation(
            token,
            TokenUser,
            user,
            required,
            &required
        )) {
        goto cleanup;
    }
    matches = EqualSid(sid, user->User.Sid) != 0;

cleanup:
    if (user != NULL) {
        SecureZeroMemory(user, required);
        HeapFree(GetProcessHeap(), 0, user);
    }
    if (token != NULL) {
        CloseHandle(token);
    }
    return matches;
}
#endif

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
#ifdef F3_BROKER_TESTING
            || sid_matches_current_token_user(owner)
#endif
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

static int trusted_mutation_writer_sid(PSID sid) {
    static const wchar_t trusted_installer_sid[] =
        L"S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464";
    return sid != NULL
        && IsValidSid(sid)
        && (
            sid_matches_well_known(sid, WinLocalSystemSid)
            || sid_matches_well_known(
                sid,
                WinBuiltinAdministratorsSid
            )
            || sid_matches_string(sid, trusted_installer_sid)
            || sid_matches_string(sid, F3_BROKER_SERVICE_SID)
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

static int dacl_mutation_writers_are_trusted(
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
        if (header->AceType == ACCESS_DENIED_ACE_TYPE) {
            continue;
        }
        if (header->AceType != ACCESS_ALLOWED_ACE_TYPE) {
            return 0;
        }
        sid = allowed_ace_sid(raw_ace, header->AceType);
        if (sid == NULL) {
            return 0;
        }
        mask = allowed_ace_mask(raw_ace, header->AceType);
        MapGenericMask(&mask, mapping);
        if ((mask & mutation_rights) != 0
            && !trusted_mutation_writer_sid(sid)) {
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
            && !dacl_mutation_writers_are_trusted(
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

static int held_directory_allows_only_trusted_mutation(HANDLE directory) {
    static const DWORD mutation_rights =
        FILE_ADD_FILE
        | FILE_ADD_SUBDIRECTORY
        | FILE_DELETE_CHILD
        | FILE_WRITE_EA
        | FILE_WRITE_ATTRIBUTES
        | DELETE
        | WRITE_DAC
        | WRITE_OWNER;
    GENERIC_MAPPING mapping = {
        FILE_GENERIC_READ,
        FILE_GENERIC_WRITE,
        FILE_GENERIC_EXECUTE,
        FILE_ALL_ACCESS
    };
    PSECURITY_DESCRIPTOR descriptor = NULL;
    PACL dacl = NULL;
    PSID owner = NULL;
    int ok = directory != INVALID_HANDLE_VALUE
        && GetSecurityInfo(
            directory,
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            &owner,
            NULL,
            &dacl,
            NULL,
            &descriptor
        ) == ERROR_SUCCESS
        && dacl != NULL
        && trusted_namespace_owner(owner)
        && dacl_mutation_writers_are_trusted(
            dacl,
            mutation_rights,
            &mapping
        );
    if (descriptor != NULL) {
        LocalFree(descriptor);
    }
    return ok;
}

static int held_file_allows_only_trusted_mutation(HANDLE file) {
    static const DWORD mutation_rights =
        FILE_WRITE_DATA
        | FILE_APPEND_DATA
        | FILE_WRITE_EA
        | FILE_WRITE_ATTRIBUTES
        | DELETE
        | WRITE_DAC
        | WRITE_OWNER;
    GENERIC_MAPPING mapping = {
        FILE_GENERIC_READ,
        FILE_GENERIC_WRITE,
        FILE_GENERIC_EXECUTE,
        FILE_ALL_ACCESS
    };
    PSECURITY_DESCRIPTOR descriptor = NULL;
    PACL dacl = NULL;
    PSID owner = NULL;
    int ok = file != INVALID_HANDLE_VALUE
        && GetSecurityInfo(
            file,
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            &owner,
            NULL,
            &dacl,
            NULL,
            &descriptor
        ) == ERROR_SUCCESS
        && dacl != NULL
        && trusted_namespace_owner(owner)
        && dacl_mutation_writers_are_trusted(
            dacl,
            mutation_rights,
            &mapping
        );
    if (descriptor != NULL) {
        LocalFree(descriptor);
    }
    return ok;
}

static int append_protected_writable_directory(
    const wchar_t *path,
    int require_file_creation,
    HeldDirectoryChain *chain
) {
    BY_HANDLE_FILE_INFORMATION information;
    FILE_ATTRIBUTE_TAG_INFO tag;
    HeldDirectory *held;
    DWORD access = READ_CONTROL | FILE_READ_ATTRIBUTES | FILE_TRAVERSE;
    if (chain == NULL
        || chain->count >= F3_MAX_HELD_DIRECTORIES
        || !strict_windows_candidate_path(path)
        || !reject_reparse_chain(path)) {
        return 0;
    }
    if (require_file_creation) {
        access |= FILE_ADD_FILE;
    }
    held = &chain->entries[chain->count];
    memset(held, 0, sizeof(*held));
    held->handle = CreateFileW(
        path,
        access,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
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
        || (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0
        || !held_directory_allows_only_trusted_mutation(held->handle)) {
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

#ifdef F3_BROKER_TESTING
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
#endif

#ifdef F3_BROKER_TESTING
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

static int parse_production_candidate(
    const unsigned char *raw,
    DWORD raw_size,
    ProductionCandidate *candidate
) {
    static const char *names[] = {
        "schema",
        "action",
        "authorization_path",
        "completion_marker_path",
        "publication_receipt_path",
        "launch_authorization_path",
        "execution_ledger_root",
        "resume_authorization_path",
        "resume_status_path",
        "signing_key_slot_id",
        "credential_slot_id",
        "runtime_manifest_schema",
        "source_manifest_schema"
    };
    const char *values[13];
    size_t lengths[13];
    size_t offset = 0;
    size_t field;
    CandidateAction action = ACTION_INVALID;
    if (raw == NULL
        || candidate == NULL
        || raw_size == 0
        || raw[raw_size - 1] != '\n') {
        return 0;
    }
    memset(candidate, 0, sizeof(*candidate));
    for (field = 0; field < raw_size; ++field) {
        if (raw[field] == '\0' || raw[field] == '\r') {
            return 0;
        }
    }
    for (field = 0; field < 13; ++field) {
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
        || !value_equals(values[0], lengths[0], F3_CANDIDATE_SCHEMA_V2)
        || !candidate_absolute_path(values[2], lengths[2])
        || !candidate_absolute_path(values[3], lengths[3])
        || !candidate_absolute_path(values[4], lengths[4])
        || !candidate_absolute_path(values[5], lengths[5])
        || !candidate_absolute_path(values[6], lengths[6])
        || !value_equals(values[9], lengths[9], F3_SIGNING_KEY_SLOT_ID)
        || !value_equals(values[10], lengths[10], F3_CREDENTIAL_SLOT_ID)
        || !value_equals(values[11], lengths[11], F3_RUNTIME_MANIFEST_SCHEMA)
        || !value_equals(values[12], lengths[12], F3_SOURCE_MANIFEST_SCHEMA)) {
        return 0;
    }
    if (value_equals(values[1], lengths[1], "run")) {
        action = ACTION_RUN;
        if (lengths[7] != 0 || lengths[8] != 0) {
            return 0;
        }
    } else if (value_equals(values[1], lengths[1], "resume")) {
        action = ACTION_RESUME;
        if (!candidate_absolute_path(values[7], lengths[7])
            || !candidate_absolute_path(values[8], lengths[8])) {
            return 0;
        }
    } else {
        return 0;
    }
    candidate->action = action;
    candidate->authorization_path.value = values[2];
    candidate->authorization_path.length = lengths[2];
    candidate->completion_marker_path.value = values[3];
    candidate->completion_marker_path.length = lengths[3];
    candidate->publication_receipt_path.value = values[4];
    candidate->publication_receipt_path.length = lengths[4];
    candidate->launch_authorization_path.value = values[5];
    candidate->launch_authorization_path.length = lengths[5];
    candidate->execution_ledger_root.value = values[6];
    candidate->execution_ledger_root.length = lengths[6];
    candidate->resume_authorization_path.value = values[7];
    candidate->resume_authorization_path.length = lengths[7];
    candidate->resume_status_path.value = values[8];
    candidate->resume_status_path.length = lengths[8];
    return 1;
}

static int byte_slice_to_wide(
    ByteSlice value,
    wchar_t *output,
    size_t capacity
) {
    int required;
    int written;
    if (value.value == NULL
        || value.length == 0
        || value.length > INT_MAX
        || output == NULL
        || capacity == 0
        || capacity > INT_MAX) {
        return 0;
    }
    required = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        value.value,
        (int)value.length,
        NULL,
        0
    );
    if (required <= 0 || (size_t)required + 1 > capacity) {
        return 0;
    }
    written = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        value.value,
        (int)value.length,
        output,
        required
    );
    if (written != required) {
        return 0;
    }
    output[required] = L'\0';
    return strict_windows_candidate_path(output);
}

static int wide_path_to_utf8(
    const wchar_t *value,
    char *output,
    size_t capacity,
    size_t *length
) {
    wchar_t normalized[32768];
    int required;
    int written;
    if (value == NULL
        || output == NULL
        || capacity == 0
        || capacity > INT_MAX
        || length == NULL) {
        return 0;
    }
    if (!canonical_path(
            value,
            normalized,
            (DWORD)(sizeof(normalized) / sizeof(normalized[0]))
        )
        || !strict_windows_candidate_path(normalized)) {
        return 0;
    }
    required = WideCharToMultiByte(
        CP_UTF8,
        WC_ERR_INVALID_CHARS,
        normalized,
        -1,
        NULL,
        0,
        NULL,
        NULL
    );
    if (required <= 1 || (size_t)required > capacity) {
        return 0;
    }
    written = WideCharToMultiByte(
        CP_UTF8,
        WC_ERR_INVALID_CHARS,
        normalized,
        -1,
        output,
        required,
        NULL,
        NULL
    );
    if (written != required) {
        SecureZeroMemory(normalized, sizeof(normalized));
        return 0;
    }
    *length = (size_t)required - 1;
    SecureZeroMemory(normalized, sizeof(normalized));
    return 1;
}

static int filename_matches_digest_suffix(
    const wchar_t *path,
    const unsigned char digest[32],
    const wchar_t *suffix
) {
    const wchar_t *filename = wcsrchr(path, L'\\');
    wchar_t expected[65];
    static const wchar_t digits[] = L"0123456789abcdef";
    size_t index;
    if (path == NULL || suffix == NULL || digest == NULL) {
        return 0;
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

static int base64_value(unsigned char value) {
    if (value >= 'A' && value <= 'Z') {
        return value - 'A';
    }
    if (value >= 'a' && value <= 'z') {
        return value - 'a' + 26;
    }
    if (value >= '0' && value <= '9') {
        return value - '0' + 52;
    }
    if (value == '+') {
        return 62;
    }
    if (value == '/') {
        return 63;
    }
    return -1;
}

static int decode_rsa3072_signature(
    const unsigned char *encoded,
    size_t encoded_size,
    unsigned char output[384]
) {
    size_t input_offset;
    size_t output_offset = 0;
    if (encoded == NULL || output == NULL || encoded_size != 512) {
        return 0;
    }
    for (input_offset = 0; input_offset < encoded_size; input_offset += 4) {
        int first = base64_value(encoded[input_offset]);
        int second = base64_value(encoded[input_offset + 1]);
        int third = base64_value(encoded[input_offset + 2]);
        int fourth = base64_value(encoded[input_offset + 3]);
        if (first < 0 || second < 0 || third < 0 || fourth < 0) {
            SecureZeroMemory(output, 384);
            return 0;
        }
        output[output_offset++] =
            (unsigned char)((first << 2) | (second >> 4));
        output[output_offset++] =
            (unsigned char)((second << 4) | (third >> 2));
        output[output_offset++] =
            (unsigned char)((third << 6) | fourth);
    }
    return output_offset == 384;
}

static int parse_signed_envelope(
    const unsigned char *raw,
    DWORD raw_size,
    SignedEnvelope *envelope
) {
    static const unsigned char prefix[] = "{\"payload\":";
    static const unsigned char marker[] = ",\"signature_base64\":\"";
    static const unsigned char suffix[] = "\"}";
    size_t marker_offset;
    size_t expected_size;
    if (raw == NULL
        || envelope == NULL
        || raw_size <= sizeof(prefix) - 1 + sizeof(marker) - 1 + 512 + 2
        || memcmp(raw, prefix, sizeof(prefix) - 1) != 0) {
        return 0;
    }
    marker_offset = (size_t)raw_size - (sizeof(marker) - 1 + 512 + sizeof(suffix) - 1);
    expected_size = marker_offset
        + sizeof(marker) - 1
        + 512
        + sizeof(suffix) - 1;
    if (expected_size != raw_size
        || marker_offset <= sizeof(prefix) - 1
        || memcmp(raw + marker_offset, marker, sizeof(marker) - 1) != 0
        || memcmp(
            raw + raw_size - (sizeof(suffix) - 1),
            suffix,
            sizeof(suffix) - 1
        ) != 0
        || raw[sizeof(prefix) - 1] != '{'
        || raw[marker_offset - 1] != '}'
        || !decode_rsa3072_signature(
            raw + marker_offset + sizeof(marker) - 1,
            512,
            envelope->signature
        )) {
        SecureZeroMemory(envelope, sizeof(*envelope));
        return 0;
    }
    envelope->payload = raw + sizeof(prefix) - 1;
    envelope->payload_size = (DWORD)(marker_offset - (sizeof(prefix) - 1));
    return 1;
}

static int json_hex(unsigned char value) {
    return (value >= '0' && value <= '9')
        || (value >= 'a' && value <= 'f')
        || (value >= 'A' && value <= 'F');
}

static int json_skip_string(
    const unsigned char *raw,
    size_t size,
    size_t *offset
) {
    size_t cursor;
    if (raw == NULL
        || offset == NULL
        || *offset >= size
        || raw[*offset] != '"') {
        return 0;
    }
    cursor = *offset + 1;
    while (cursor < size) {
        unsigned char value = raw[cursor++];
        if (value == '"') {
            *offset = cursor;
            return 1;
        }
        if (value < 0x20) {
            return 0;
        }
        if (value == '\\') {
            unsigned char escaped;
            if (cursor >= size) {
                return 0;
            }
            escaped = raw[cursor++];
            if (escaped == 'u') {
                size_t index;
                if (cursor + 4 > size) {
                    return 0;
                }
                for (index = 0; index < 4; ++index) {
                    if (!json_hex(raw[cursor + index])) {
                        return 0;
                    }
                }
                cursor += 4;
            } else if (escaped != '"'
                && escaped != '\\'
                && escaped != '/'
                && escaped != 'b'
                && escaped != 'f'
                && escaped != 'n'
                && escaped != 'r'
                && escaped != 't') {
                return 0;
            }
        }
    }
    return 0;
}

static int json_skip_value(
    const unsigned char *raw,
    size_t size,
    size_t *offset,
    unsigned int depth
) {
    size_t cursor;
    if (raw == NULL || offset == NULL || *offset >= size || depth > 64) {
        return 0;
    }
    cursor = *offset;
    if (raw[cursor] == '"') {
        return json_skip_string(raw, size, offset);
    }
    if (raw[cursor] == '{') {
        ++cursor;
        if (cursor < size && raw[cursor] == '}') {
            *offset = cursor + 1;
            return 1;
        }
        for (;;) {
            if (!json_skip_string(raw, size, &cursor)
                || cursor >= size
                || raw[cursor++] != ':'
                || !json_skip_value(raw, size, &cursor, depth + 1)
                || cursor >= size) {
                return 0;
            }
            if (raw[cursor] == '}') {
                *offset = cursor + 1;
                return 1;
            }
            if (raw[cursor++] != ',') {
                return 0;
            }
        }
    }
    if (raw[cursor] == '[') {
        ++cursor;
        if (cursor < size && raw[cursor] == ']') {
            *offset = cursor + 1;
            return 1;
        }
        for (;;) {
            if (!json_skip_value(raw, size, &cursor, depth + 1)
                || cursor >= size) {
                return 0;
            }
            if (raw[cursor] == ']') {
                *offset = cursor + 1;
                return 1;
            }
            if (raw[cursor++] != ',') {
                return 0;
            }
        }
    }
    if (cursor + 4 <= size
        && (memcmp(raw + cursor, "true", 4) == 0
            || memcmp(raw + cursor, "null", 4) == 0)) {
        *offset = cursor + 4;
        return 1;
    }
    if (cursor + 5 <= size && memcmp(raw + cursor, "false", 5) == 0) {
        *offset = cursor + 5;
        return 1;
    }
    if (raw[cursor] == '-') {
        ++cursor;
        if (cursor >= size) {
            return 0;
        }
    }
    if (raw[cursor] == '0') {
        ++cursor;
        if (cursor < size && raw[cursor] >= '0' && raw[cursor] <= '9') {
            return 0;
        }
    } else if (raw[cursor] >= '1' && raw[cursor] <= '9') {
        do {
            ++cursor;
        } while (cursor < size && raw[cursor] >= '0' && raw[cursor] <= '9');
    } else {
        return 0;
    }
    if (cursor < size && raw[cursor] == '.') {
        ++cursor;
        if (cursor >= size || raw[cursor] < '0' || raw[cursor] > '9') {
            return 0;
        }
        do {
            ++cursor;
        } while (cursor < size && raw[cursor] >= '0' && raw[cursor] <= '9');
    }
    if (cursor < size && (raw[cursor] == 'e' || raw[cursor] == 'E')) {
        ++cursor;
        if (cursor < size && (raw[cursor] == '+' || raw[cursor] == '-')) {
            ++cursor;
        }
        if (cursor >= size || raw[cursor] < '0' || raw[cursor] > '9') {
            return 0;
        }
        do {
            ++cursor;
        } while (cursor < size && raw[cursor] >= '0' && raw[cursor] <= '9');
    }
    *offset = cursor;
    return 1;
}

static int json_top_field(
    const unsigned char *payload,
    size_t payload_size,
    const char *name,
    ByteSlice *value
) {
    size_t cursor = 1;
    size_t name_length = strlen(name);
    char previous[128];
    size_t previous_length = 0;
    int found = 0;
    if (payload == NULL
        || name == NULL
        || value == NULL
        || payload_size < 2
        || payload[0] != '{'
        || payload[payload_size - 1] != '}') {
        return 0;
    }
    memset(previous, 0, sizeof(previous));
    memset(value, 0, sizeof(*value));
    if (cursor == payload_size - 1) {
        return 0;
    }
    for (;;) {
        size_t key_start;
        size_t key_end;
        size_t value_start;
        size_t value_end;
        size_t key_length;
        size_t common;
        int order = 0;
        if (cursor >= payload_size - 1 || payload[cursor] != '"') {
            return 0;
        }
        key_start = cursor + 1;
        if (!json_skip_string(payload, payload_size, &cursor)) {
            return 0;
        }
        key_end = cursor - 1;
        key_length = key_end - key_start;
        if (key_length == 0
            || key_length >= sizeof(previous)
            || memchr(payload + key_start, '\\', key_length) != NULL
            || cursor >= payload_size
            || payload[cursor++] != ':') {
            return 0;
        }
        if (previous_length != 0) {
            common = previous_length < key_length ? previous_length : key_length;
            order = memcmp(previous, payload + key_start, common);
            if (order > 0 || (order == 0 && previous_length >= key_length)) {
                return 0;
            }
        }
        memcpy(previous, payload + key_start, key_length);
        previous[key_length] = '\0';
        previous_length = key_length;
        value_start = cursor;
        if (!json_skip_value(payload, payload_size, &cursor, 0)) {
            return 0;
        }
        value_end = cursor;
        if (key_length == name_length
            && memcmp(payload + key_start, name, name_length) == 0) {
            value->value = (const char *)payload + value_start;
            value->length = value_end - value_start;
            found = 1;
        }
        if (cursor >= payload_size) {
            return 0;
        }
        if (payload[cursor] == '}') {
            return found && cursor == payload_size - 1;
        }
        if (payload[cursor++] != ',') {
            return 0;
        }
    }
}

static int json_string_matches(
    ByteSlice json_value,
    const char *expected,
    size_t expected_length
) {
    size_t input = 1;
    size_t output = 0;
    if (json_value.value == NULL
        || expected == NULL
        || json_value.length < 2
        || json_value.value[0] != '"'
        || json_value.value[json_value.length - 1] != '"') {
        return 0;
    }
    while (input + 1 < json_value.length) {
        unsigned char value = (unsigned char)json_value.value[input++];
        if (value == '\\') {
            if (input + 1 > json_value.length
                || json_value.value[input++] != '\\') {
                return 0;
            }
            value = '\\';
        } else if (value == '"') {
            return 0;
        }
        if (output >= expected_length
            || value != (unsigned char)expected[output++]) {
            return 0;
        }
    }
    return output == expected_length;
}

static int json_top_string_matches(
    const unsigned char *payload,
    size_t payload_size,
    const char *name,
    const char *expected,
    size_t expected_length
) {
    ByteSlice value;
    return json_top_field(payload, payload_size, name, &value)
        && json_string_matches(value, expected, expected_length);
}

static int json_top_is_null(
    const unsigned char *payload,
    size_t payload_size,
    const char *name
) {
    ByteSlice value;
    return json_top_field(payload, payload_size, name, &value)
        && value.length == 4
        && memcmp(value.value, "null", 4) == 0;
}

static int json_top_has_exact_keys(
    const unsigned char *payload,
    size_t payload_size,
    const char *const *names,
    size_t count
) {
    size_t cursor = 1;
    size_t field = 0;
    if (payload == NULL
        || names == NULL
        || count == 0
        || payload_size < 2
        || payload[0] != '{'
        || payload[payload_size - 1] != '}') {
        return 0;
    }
    while (field < count) {
        size_t key_start;
        size_t key_end;
        size_t key_length;
        size_t expected_length = strlen(names[field]);
        if (cursor >= payload_size - 1 || payload[cursor] != '"') {
            return 0;
        }
        key_start = cursor + 1;
        if (!json_skip_string(payload, payload_size, &cursor)) {
            return 0;
        }
        key_end = cursor - 1;
        key_length = key_end - key_start;
        if (key_length != expected_length
            || memcmp(payload + key_start, names[field], key_length) != 0
            || cursor >= payload_size
            || payload[cursor++] != ':'
            || !json_skip_value(payload, payload_size, &cursor, 0)) {
            return 0;
        }
        ++field;
        if (field == count) {
            return cursor == payload_size - 1 && payload[cursor] == '}';
        }
        if (cursor >= payload_size || payload[cursor++] != ',') {
            return 0;
        }
    }
    return 0;
}

static int json_top_has_sha256(
    const unsigned char *payload,
    size_t payload_size,
    const char *name
) {
    ByteSlice value;
    size_t index;
    if (!json_top_field(payload, payload_size, name, &value)
        || value.length != 66
        || value.value[0] != '"'
        || value.value[65] != '"') {
        return 0;
    }
    for (index = 1; index < 65; ++index) {
        unsigned char character = (unsigned char)value.value[index];
        if (!((character >= '0' && character <= '9')
                || (character >= 'a' && character <= 'f'))) {
            return 0;
        }
    }
    return 1;
}

static int json_top_copy_sha256(
    const unsigned char *payload,
    size_t payload_size,
    const char *name,
    char output[65]
) {
    ByteSlice value;
    if (output == NULL
        || !json_top_has_sha256(payload, payload_size, name)
        || !json_top_field(payload, payload_size, name, &value)) {
        return 0;
    }
    memcpy(output, value.value + 1, 64);
    output[64] = '\0';
    return 1;
}

static int json_top_uint32(
    const unsigned char *payload,
    size_t payload_size,
    const char *name,
    DWORD *output
) {
    ByteSlice value;
    size_t index;
    uint64_t observed = 0;
    if (output == NULL
        || !json_top_field(payload, payload_size, name, &value)
        || value.length == 0
        || (value.length > 1 && value.value[0] == '0')) {
        return 0;
    }
    for (index = 0; index < value.length; ++index) {
        unsigned char character = (unsigned char)value.value[index];
        if (character < '0' || character > '9') {
            return 0;
        }
        observed = observed * 10 + (uint64_t)(character - '0');
        if (observed > UINT_MAX) {
            return 0;
        }
    }
    *output = (DWORD)observed;
    return 1;
}

static int hex_value(char value) {
    if (value >= '0' && value <= '9') {
        return value - '0';
    }
    if (value >= 'a' && value <= 'f') {
        return value - 'a' + 10;
    }
    if (value >= 'A' && value <= 'F') {
        return value - 'A' + 10;
    }
    return -1;
}

static int verify_execution_signature(const SignedEnvelope *envelope) {
    BCRYPT_ALG_HANDLE algorithm = NULL;
    BCRYPT_KEY_HANDLE key = NULL;
    BCRYPT_PKCS1_PADDING_INFO padding = {BCRYPT_SHA256_ALGORITHM};
    unsigned char digest[32];
    unsigned char blob[
        sizeof(BCRYPT_RSAKEY_BLOB) + sizeof(DWORD) + F3_EXECUTION_RSA_BITS / 8
    ];
    BCRYPT_RSAKEY_BLOB *header = (BCRYPT_RSAKEY_BLOB *)blob;
    unsigned char *exponent_bytes = blob + sizeof(*header);
    unsigned char *modulus = exponent_bytes + sizeof(DWORD);
    DWORD exponent = F3_BROKER_EXECUTION_PUBLIC_EXPONENT;
    size_t index;
    int ok = 0;
    if (envelope == NULL
        || strlen(F3_BROKER_EXECUTION_PUBLIC_MODULUS_HEX)
            != (F3_EXECUTION_RSA_BITS / 8) * 2
        || exponent < 3
        || (exponent & 1u) == 0
        || !hash_memory(
            envelope->payload,
            envelope->payload_size,
            digest
        )) {
        return 0;
    }
    memset(blob, 0, sizeof(blob));
    header->Magic = BCRYPT_RSAPUBLIC_MAGIC;
    header->BitLength = F3_EXECUTION_RSA_BITS;
    header->cbPublicExp = sizeof(DWORD);
    header->cbModulus = F3_EXECUTION_RSA_BITS / 8;
    exponent_bytes[0] = (unsigned char)(exponent >> 24);
    exponent_bytes[1] = (unsigned char)(exponent >> 16);
    exponent_bytes[2] = (unsigned char)(exponent >> 8);
    exponent_bytes[3] = (unsigned char)exponent;
    for (index = 0; index < F3_EXECUTION_RSA_BITS / 8; ++index) {
        int high = hex_value(F3_BROKER_EXECUTION_PUBLIC_MODULUS_HEX[index * 2]);
        int low = hex_value(F3_BROKER_EXECUTION_PUBLIC_MODULUS_HEX[index * 2 + 1]);
        if (high < 0 || low < 0) {
            goto cleanup;
        }
        modulus[index] = (unsigned char)((high << 4) | low);
    }
    if (BCryptOpenAlgorithmProvider(
            &algorithm,
            BCRYPT_RSA_ALGORITHM,
            NULL,
            0
        ) < 0
        || BCryptImportKeyPair(
            algorithm,
            NULL,
            BCRYPT_RSAPUBLIC_BLOB,
            &key,
            blob,
            sizeof(blob),
            0
        ) < 0
        || BCryptVerifySignature(
            key,
            &padding,
            digest,
            sizeof(digest),
            (PUCHAR)envelope->signature,
            sizeof(envelope->signature),
            BCRYPT_PAD_PKCS1
        ) < 0) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (key != NULL) {
        BCryptDestroyKey(key);
    }
    if (algorithm != NULL) {
        BCryptCloseAlgorithmProvider(algorithm, 0);
    }
    SecureZeroMemory(digest, sizeof(digest));
    SecureZeroMemory(blob, sizeof(blob));
    return ok;
}

static int validate_current_launch_payload(
    const SignedEnvelope *envelope,
    const ProductionCandidate *candidate
) {
    const char *action = candidate->action == ACTION_RUN ? "run" : "resume";
    char credential[32768 * 3];
    char runtime[32768 * 3];
    char source[32768 * 3];
    size_t credential_length = 0;
    size_t runtime_length = 0;
    size_t source_length = 0;
    if (envelope == NULL
        || candidate == NULL
        || !wide_path_to_utf8(
            F3_BROKER_CREDENTIAL_SLOT_PATH,
            credential,
            sizeof(credential),
            &credential_length
        )
        || !wide_path_to_utf8(
            F3_BROKER_RUNTIME_PATH,
            runtime,
            sizeof(runtime),
            &runtime_length
        )
        || !wide_path_to_utf8(
            F3_BROKER_SOURCE_PATH,
            source,
            sizeof(source),
            &source_length
        )
        || !json_top_string_matches(
            envelope->payload,
            envelope->payload_size,
            "schema",
            "factor-v3-formal-supervisor-launch-authorization/v2",
            strlen("factor-v3-formal-supervisor-launch-authorization/v2")
        )
        || !json_top_string_matches(
            envelope->payload,
            envelope->payload_size,
            "action",
            action,
            strlen(action)
        )
        || !json_top_string_matches(
            envelope->payload,
            envelope->payload_size,
            "bootstrap_execution_authorization_path",
            candidate->authorization_path.value,
            candidate->authorization_path.length
        )
        || !json_top_string_matches(
            envelope->payload,
            envelope->payload_size,
            "publication_completion_marker_path",
            candidate->completion_marker_path.value,
            candidate->completion_marker_path.length
        )
        || !json_top_string_matches(
            envelope->payload,
            envelope->payload_size,
            "supervisor_publication_receipt_path",
            candidate->publication_receipt_path.value,
            candidate->publication_receipt_path.length
        )
        || !json_top_string_matches(
            envelope->payload,
            envelope->payload_size,
            "execution_ledger_root",
            candidate->execution_ledger_root.value,
            candidate->execution_ledger_root.length
        )
        || !json_top_string_matches(
            envelope->payload,
            envelope->payload_size,
            "credential_path",
            credential,
            credential_length
        )
        || !json_top_string_matches(
            envelope->payload,
            envelope->payload_size,
            "credential_slot_id",
            F3_CREDENTIAL_SLOT_ID,
            strlen(F3_CREDENTIAL_SLOT_ID)
        )
        || !json_top_string_matches(
            envelope->payload,
            envelope->payload_size,
            "python_executable_path",
            runtime,
            runtime_length
        )
        || !json_top_string_matches(
            envelope->payload,
            envelope->payload_size,
            "supervisor_loader_path",
            source,
            source_length
        )
        || !verify_execution_signature(envelope)) {
        SecureZeroMemory(credential, sizeof(credential));
        SecureZeroMemory(runtime, sizeof(runtime));
        SecureZeroMemory(source, sizeof(source));
        return 0;
    }
    SecureZeroMemory(credential, sizeof(credential));
    SecureZeroMemory(runtime, sizeof(runtime));
    SecureZeroMemory(source, sizeof(source));
    if (candidate->action == ACTION_RUN) {
        return json_top_is_null(
                envelope->payload,
                envelope->payload_size,
                "resume_of_authorization_sha256"
            )
            && json_top_is_null(
                envelope->payload,
                envelope->payload_size,
                "resume_status_path"
            );
    }
    return json_top_string_matches(
            envelope->payload,
            envelope->payload_size,
            "resume_status_path",
            candidate->resume_status_path.value,
            candidate->resume_status_path.length
        );
}

static int json_top_plain_string(
    const unsigned char *payload,
    size_t payload_size,
    const char *name,
    ByteSlice *value
) {
    ByteSlice json_value;
    if (!json_top_field(payload, payload_size, name, &json_value)
        || json_value.length < 2
        || json_value.value[0] != '"'
        || json_value.value[json_value.length - 1] != '"'
        || memchr(
            json_value.value + 1,
            '\\',
            json_value.length - 2
        ) != NULL) {
        return 0;
    }
    value->value = json_value.value + 1;
    value->length = json_value.length - 2;
    return 1;
}

static int json_top_strings_equal(
    const SignedEnvelope *left,
    const char *left_name,
    const SignedEnvelope *right,
    const char *right_name
) {
    ByteSlice left_value;
    ByteSlice right_value;
    return left != NULL
        && right != NULL
        && json_top_plain_string(
            left->payload,
            left->payload_size,
            left_name,
            &left_value
        )
        && json_top_plain_string(
            right->payload,
            right->payload_size,
            right_name,
            &right_value
        )
        && left_value.length == right_value.length
        && memcmp(left_value.value, right_value.value, left_value.length) == 0;
}

static int validate_original_launch_payload(
    const SignedEnvelope *original,
    const ProductionCandidate *candidate
) {
    char credential[32768 * 3];
    size_t credential_length = 0;
    int ok = original != NULL
        && candidate != NULL
        && wide_path_to_utf8(
            F3_BROKER_CREDENTIAL_SLOT_PATH,
            credential,
            sizeof(credential),
            &credential_length
        )
        && json_top_string_matches(
            original->payload,
            original->payload_size,
            "schema",
            "factor-v3-formal-supervisor-launch-authorization/v2",
            strlen("factor-v3-formal-supervisor-launch-authorization/v2")
        )
        && json_top_string_matches(
            original->payload,
            original->payload_size,
            "action",
            "run",
            strlen("run")
        )
        && json_top_string_matches(
            original->payload,
            original->payload_size,
            "bootstrap_execution_authorization_path",
            candidate->authorization_path.value,
            candidate->authorization_path.length
        )
        && json_top_string_matches(
            original->payload,
            original->payload_size,
            "publication_completion_marker_path",
            candidate->completion_marker_path.value,
            candidate->completion_marker_path.length
        )
        && json_top_string_matches(
            original->payload,
            original->payload_size,
            "supervisor_publication_receipt_path",
            candidate->publication_receipt_path.value,
            candidate->publication_receipt_path.length
        )
        && json_top_string_matches(
            original->payload,
            original->payload_size,
            "execution_ledger_root",
            candidate->execution_ledger_root.value,
            candidate->execution_ledger_root.length
        )
        && json_top_string_matches(
            original->payload,
            original->payload_size,
            "credential_path",
            credential,
            credential_length
        )
        && json_top_string_matches(
            original->payload,
            original->payload_size,
            "credential_slot_id",
            F3_CREDENTIAL_SLOT_ID,
            strlen(F3_CREDENTIAL_SLOT_ID)
        )
        && json_top_is_null(
            original->payload,
            original->payload_size,
            "resume_of_authorization_sha256"
        )
        && json_top_is_null(
            original->payload,
            original->payload_size,
            "resume_status_path"
        )
        && verify_execution_signature(original);
    SecureZeroMemory(credential, sizeof(credential));
    return ok;
}

static int expected_ledger_path(
    ByteSlice ledger_root,
    const char *category,
    const char authorization_sha256[65],
    wchar_t *output,
    size_t capacity
) {
    wchar_t root[32768];
    wchar_t wide_category[64];
    wchar_t wide_sha256[65];
    int category_length;
    int sha_length;
    int written;
    if (!byte_slice_to_wide(
            ledger_root,
            root,
            sizeof(root) / sizeof(root[0])
        )
        || category == NULL
        || authorization_sha256 == NULL
        || output == NULL
        || capacity == 0
        || capacity > INT_MAX) {
        return 0;
    }
    category_length = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        category,
        -1,
        wide_category,
        (int)(sizeof(wide_category) / sizeof(wide_category[0]))
    );
    sha_length = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        authorization_sha256,
        -1,
        wide_sha256,
        (int)(sizeof(wide_sha256) / sizeof(wide_sha256[0]))
    );
    if (category_length <= 1 || sha_length != 65) {
        return 0;
    }
    written = _snwprintf_s(
        output,
        capacity,
        _TRUNCATE,
        L"%ls\\%ls\\sha256\\%lc%lc\\%ls.json",
        root,
        wide_category,
        wide_sha256[0],
        wide_sha256[1],
        wide_sha256
    );
    SecureZeroMemory(root, sizeof(root));
    SecureZeroMemory(wide_category, sizeof(wide_category));
    SecureZeroMemory(wide_sha256, sizeof(wide_sha256));
    return written > 0
        && (size_t)written < capacity
        && strict_windows_candidate_path(output);
}

static int hold_protected_completion_namespace(
    ByteSlice ledger_root,
    const char launch_authorization_sha256[65],
    wchar_t completed_path[32768],
    ProtectedCompletionNamespace *completion
) {
    wchar_t root[32768];
    wchar_t native_root[32768];
    wchar_t sha256_root[32768];
    wchar_t parent[32768];
    wchar_t observed_native_root[32768];
    const wchar_t *filename;
    int written;
    int ok = 0;
    if (completion == NULL
        || launch_authorization_sha256 == NULL
        || completed_path == NULL) {
        return 0;
    }
    memset(completion, 0, sizeof(*completion));
    initialize_held_directory_chain(&completion->chain);
    if (!byte_slice_to_wide(
            ledger_root,
            root,
            sizeof(root) / sizeof(root[0])
        )
        || !expected_ledger_path(
            ledger_root,
            "native_completed",
            launch_authorization_sha256,
            completed_path,
            32768
        )) {
        goto cleanup;
    }
    written = _snwprintf_s(
        observed_native_root,
        sizeof(observed_native_root) / sizeof(observed_native_root[0]),
        _TRUNCATE,
        L"%ls\\native_completed",
        root
    );
    if (written <= 0
        || (size_t)written
            >= sizeof(observed_native_root) / sizeof(observed_native_root[0])
        || !canonical_path(
            observed_native_root,
            native_root,
            (DWORD)(sizeof(native_root) / sizeof(native_root[0]))
        )
        || !parent_directory(
            completed_path,
            parent,
            sizeof(parent) / sizeof(parent[0])
        )
        || !parent_directory(
            parent,
            sha256_root,
            sizeof(sha256_root) / sizeof(sha256_root[0])
        )
        || !parent_directory(
            sha256_root,
            observed_native_root,
            sizeof(observed_native_root)
                / sizeof(observed_native_root[0])
        )
        || _wcsicmp(observed_native_root, native_root) != 0) {
        goto cleanup;
    }
    filename = wcsrchr(completed_path, L'\\');
    if (filename == NULL
        || wcslen(filename + 1) != 69
        || wcscmp(filename + 65, L".json") != 0
        || wcsncmp(
            filename + 1,
            parent + wcslen(parent) - 2,
            2
        ) != 0
        || wcscpy_s(
            completion->final_name,
            sizeof(completion->final_name)
                / sizeof(completion->final_name[0]),
            filename + 1
        ) != 0
        || !append_protected_writable_directory(
            native_root,
            0,
            &completion->chain
        )
        || !append_protected_writable_directory(
            sha256_root,
            1,
            &completion->chain
        )
        || !append_protected_writable_directory(
            parent,
            1,
            &completion->chain
        )
        || !held_directory_chain_unchanged(&completion->chain)) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (!ok) {
        close_held_directory_chain(&completion->chain);
        SecureZeroMemory(completion, sizeof(*completion));
        initialize_held_directory_chain(&completion->chain);
        SecureZeroMemory(completed_path, 32768 * sizeof(wchar_t));
    }
    SecureZeroMemory(root, sizeof(root));
    SecureZeroMemory(native_root, sizeof(native_root));
    SecureZeroMemory(sha256_root, sizeof(sha256_root));
    SecureZeroMemory(parent, sizeof(parent));
    SecureZeroMemory(observed_native_root, sizeof(observed_native_root));
    return ok;
}

static int validate_resume_status(
    const unsigned char *status,
    DWORD status_size,
    const SignedEnvelope *original,
    const char original_sha256[65]
) {
    static const char *const status_keys[] = {
        "action",
        "authorization_id_sha256",
        "authorization_nonce_sha256",
        "bootstrap_execution_authorization_sha256",
        "launch_authorization_sha256",
        "replay_scope",
        "schema",
        "status",
    };
    SignedEnvelope status_view;
    memset(&status_view, 0, sizeof(status_view));
    status_view.payload = status;
    status_view.payload_size = status_size;
    return status != NULL
        && status_size > 0
        && json_top_has_exact_keys(
            status,
            status_size,
            status_keys,
            sizeof(status_keys) / sizeof(status_keys[0])
        )
        && json_top_string_matches(
            status,
            status_size,
            "schema",
            "factor-v3-formal-supervisor-execution-claim/v1",
            strlen("factor-v3-formal-supervisor-execution-claim/v1")
        )
        && json_top_string_matches(
            status,
            status_size,
            "status",
            "claimed",
            strlen("claimed")
        )
        && json_top_string_matches(
            status,
            status_size,
            "action",
            "run",
            strlen("run")
        )
        && json_top_string_matches(
            status,
            status_size,
            "launch_authorization_sha256",
            original_sha256,
            64
        )
        && json_top_strings_equal(
            &status_view,
            "authorization_id_sha256",
            original,
            "authorization_id_sha256"
        )
        && json_top_strings_equal(
            &status_view,
            "authorization_nonce_sha256",
            original,
            "authorization_nonce_sha256"
        )
        && json_top_strings_equal(
            &status_view,
            "bootstrap_execution_authorization_sha256",
            original,
            "bootstrap_execution_authorization_sha256"
        )
        && json_top_strings_equal(
            &status_view,
            "replay_scope",
            original,
            "replay_scope"
        );
}

static int validate_resume_lineage(
    const ProductionCandidate *candidate,
    const SignedEnvelope *current
) {
    HeldFile original_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile status_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    unsigned char original_digest[32];
    unsigned char status_digest[32];
    unsigned char *original_raw = NULL;
    unsigned char *status_raw = NULL;
    DWORD original_size = 0;
    DWORD status_size = 0;
    SignedEnvelope original;
    wchar_t original_path[32768];
    wchar_t status_path[32768];
    wchar_t expected_status_path[32768];
    wchar_t completed_path[32768];
    char original_sha256[65];
    char status_sha256[65];
    DWORD completed_attributes;
    int ok = 0;
    memset(&original, 0, sizeof(original));
    memset(original_path, 0, sizeof(original_path));
    memset(status_path, 0, sizeof(status_path));
    memset(expected_status_path, 0, sizeof(expected_status_path));
    memset(completed_path, 0, sizeof(completed_path));
    memset(original_sha256, 0, sizeof(original_sha256));
    memset(status_sha256, 0, sizeof(status_sha256));
#ifdef F3_BROKER_TESTING
    f3_test_production_validation_stage = 41;
#endif
    if (candidate == NULL
        || current == NULL
        || candidate->action != ACTION_RESUME
        || !byte_slice_to_wide(
            candidate->resume_authorization_path,
            original_path,
            sizeof(original_path) / sizeof(original_path[0])
        )
        || !open_held_file(
            original_path,
            F3_MAX_AUTHORIZATION_BYTES,
            &original_file
        )
        || !hash_held_file(&original_file, original_digest)
        || !filename_matches_digest_suffix(
            original_path,
            original_digest,
            L".json"
        )
        || !read_candidate(&original_file, &original_raw, &original_size)
        || !parse_signed_envelope(original_raw, original_size, &original)
        || !validate_original_launch_payload(&original, candidate)) {
        goto cleanup;
    }
    digest_to_ascii(original_digest, original_sha256);
#ifdef F3_BROKER_TESTING
    f3_test_production_validation_stage = 42;
#endif
    if (!json_top_string_matches(
            current->payload,
            current->payload_size,
            "resume_of_authorization_sha256",
            original_sha256,
            64
        )
        || !json_top_strings_equal(
            current,
            "authorization_id_sha256",
            &original,
            "authorization_id_sha256"
        )
        || !json_top_strings_equal(
            current,
            "authorization_nonce_sha256",
            &original,
            "authorization_nonce_sha256"
        )
        || !json_top_strings_equal(
            current,
            "resume_of_authorization_id_sha256",
            &original,
            "authorization_id_sha256"
        )
        || !json_top_strings_equal(
            current,
            "resume_of_authorization_nonce_sha256",
            &original,
            "authorization_nonce_sha256"
        )
        || !json_top_strings_equal(
            current,
            "bootstrap_execution_authorization_sha256",
            &original,
            "bootstrap_execution_authorization_sha256"
        )
        || !json_top_strings_equal(
            current,
            "resume_of_bootstrap_execution_authorization_sha256",
            &original,
            "bootstrap_execution_authorization_sha256"
        )
        || !json_top_strings_equal(
            current,
            "replay_scope",
            &original,
            "replay_scope"
        )
        || !json_top_strings_equal(
            current,
            "resume_of_replay_scope",
            &original,
            "replay_scope"
        )
        || !byte_slice_to_wide(
            candidate->resume_status_path,
            status_path,
            sizeof(status_path) / sizeof(status_path[0])
        )) {
        goto cleanup;
    }
#ifdef F3_BROKER_TESTING
    f3_test_production_validation_stage = 43;
#endif
    if (!expected_ledger_path(
            candidate->execution_ledger_root,
            "claims",
            original_sha256,
            expected_status_path,
            sizeof(expected_status_path) / sizeof(expected_status_path[0])
        )
        || _wcsicmp(status_path, expected_status_path) != 0
        || !open_held_file(
            status_path,
            F3_MAX_AUTHORIZATION_BYTES,
            &status_file
        )
        || !hash_held_file(&status_file, status_digest)
        || !read_candidate(&status_file, &status_raw, &status_size)) {
        goto cleanup;
    }
    digest_to_ascii(status_digest, status_sha256);
#ifdef F3_BROKER_TESTING
    f3_test_production_validation_stage = 44;
#endif
    if (!json_top_string_matches(
            current->payload,
            current->payload_size,
            "resume_status_sha256",
            status_sha256,
            64
        )
        || !validate_resume_status(
            status_raw,
            status_size,
            &original,
            original_sha256
        )
        || !expected_ledger_path(
            candidate->execution_ledger_root,
            "completed",
            original_sha256,
            completed_path,
            sizeof(completed_path) / sizeof(completed_path[0])
        )) {
        goto cleanup;
    }
    SetLastError(ERROR_SUCCESS);
    completed_attributes = GetFileAttributesW(completed_path);
    if (completed_attributes != INVALID_FILE_ATTRIBUTES
        || (GetLastError() != ERROR_FILE_NOT_FOUND
            && GetLastError() != ERROR_PATH_NOT_FOUND)
        || !held_unchanged(&status_file, NULL)
        || !held_unchanged(&original_file, NULL)) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (status_raw != NULL) {
        SecureZeroMemory(status_raw, (SIZE_T)status_size + 1);
        HeapFree(GetProcessHeap(), 0, status_raw);
    }
    if (original_raw != NULL) {
        SecureZeroMemory(original_raw, (SIZE_T)original_size + 1);
        HeapFree(GetProcessHeap(), 0, original_raw);
    }
    close_held(&status_file);
    close_held(&original_file);
    SecureZeroMemory(original_digest, sizeof(original_digest));
    SecureZeroMemory(status_digest, sizeof(status_digest));
    SecureZeroMemory(&original, sizeof(original));
    SecureZeroMemory(original_path, sizeof(original_path));
    SecureZeroMemory(status_path, sizeof(status_path));
    SecureZeroMemory(expected_status_path, sizeof(expected_status_path));
    SecureZeroMemory(completed_path, sizeof(completed_path));
    SecureZeroMemory(original_sha256, sizeof(original_sha256));
    SecureZeroMemory(status_sha256, sizeof(status_sha256));
    return ok;
}

int f3_broker_validate_production_candidate(const wchar_t *candidate_path) {
    HeldFile candidate_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile launch_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    unsigned char candidate_digest[32];
    unsigned char launch_digest[32];
    unsigned char *candidate_raw = NULL;
    unsigned char *launch_raw = NULL;
    DWORD candidate_size = 0;
    DWORD launch_size = 0;
    ProductionCandidate candidate;
    SignedEnvelope envelope;
    wchar_t launch_path[32768];
    int ok = 0;
    memset(&candidate, 0, sizeof(candidate));
    memset(&envelope, 0, sizeof(envelope));
    memset(launch_path, 0, sizeof(launch_path));
#ifdef F3_BROKER_TESTING
    f3_test_production_validation_stage = 1;
#endif
    if (!open_held_file(
            candidate_path,
            F3_MAX_CANDIDATE_BYTES,
            &candidate_file
        )
        || !hash_held_file(&candidate_file, candidate_digest)
        || !filename_matches_candidate_digest(candidate_path, candidate_digest)
        || !read_candidate(
            &candidate_file,
            &candidate_raw,
            &candidate_size
        )
        || !parse_production_candidate(
            candidate_raw,
            candidate_size,
            &candidate
        )) {
        goto cleanup;
    }
#ifdef F3_BROKER_TESTING
    f3_test_production_validation_stage = 2;
#endif
    if (!byte_slice_to_wide(
            candidate.launch_authorization_path,
            launch_path,
            sizeof(launch_path) / sizeof(launch_path[0])
        )
        || !open_held_file(
            launch_path,
            F3_MAX_AUTHORIZATION_BYTES,
            &launch_file
        )
        || !hash_held_file(&launch_file, launch_digest)
        || !filename_matches_digest_suffix(
            launch_path,
            launch_digest,
            L".json"
        )
        || !read_candidate(&launch_file, &launch_raw, &launch_size)) {
        goto cleanup;
    }
#ifdef F3_BROKER_TESTING
    f3_test_production_validation_stage = 3;
#endif
    if (!parse_signed_envelope(launch_raw, launch_size, &envelope)) {
        goto cleanup;
    }
#ifdef F3_BROKER_TESTING
    f3_test_production_validation_stage = 4;
#endif
    if (!validate_current_launch_payload(&envelope, &candidate)
        || (candidate.action == ACTION_RESUME
            && !validate_resume_lineage(&candidate, &envelope))) {
        goto cleanup;
    }
#ifdef F3_BROKER_TESTING
    f3_test_production_validation_stage = 5;
#endif
    if (!held_unchanged(&launch_file, NULL)
        || !held_unchanged(&candidate_file, NULL)) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (launch_raw != NULL) {
        SecureZeroMemory(launch_raw, (SIZE_T)launch_size + 1);
        HeapFree(GetProcessHeap(), 0, launch_raw);
    }
    if (candidate_raw != NULL) {
        SecureZeroMemory(candidate_raw, (SIZE_T)candidate_size + 1);
        HeapFree(GetProcessHeap(), 0, candidate_raw);
    }
    close_held(&launch_file);
    close_held(&candidate_file);
    SecureZeroMemory(candidate_digest, sizeof(candidate_digest));
    SecureZeroMemory(launch_digest, sizeof(launch_digest));
    SecureZeroMemory(&candidate, sizeof(candidate));
    SecureZeroMemory(&envelope, sizeof(envelope));
    SecureZeroMemory(launch_path, sizeof(launch_path));
    return ok;
}

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

static int production_supervisor_command_line(
    const wchar_t *authorization_path,
    wchar_t *output,
    size_t capacity
) {
    const wchar_t *arguments[] = {
        F3_BROKER_RUNTIME_PATH,
        L"-I",
        L"-B",
        L"-S",
        L"-P",
        F3_BROKER_SOURCE_PATH,
        authorization_path,
        L"--native-broker-v1",
    };
    size_t offset = 0;
    size_t index;
    for (index = 0; index < sizeof(arguments) / sizeof(arguments[0]); ++index) {
        if ((index != 0
                && !append_command_character(
                    output,
                    capacity,
                    &offset,
                    L' '
                ))
            || !append_quoted_argument(
                arguments[index],
                output,
                capacity,
                &offset
            )) {
            return 0;
        }
    }
    if (offset >= capacity) {
        return 0;
    }
    output[offset] = L'\0';
    return 1;
}

static int create_production_restricted_token(HANDLE *restricted_token) {
    HANDLE primary = NULL;
    PSID disabled_sid = NULL;
    PSID worker_sid = NULL;
    SID_AND_ATTRIBUTES disabled;
    SID_AND_ATTRIBUTES restricting;
    TOKEN_GROUPS *restricted = NULL;
    DWORD restricted_size = 0;
    int ok = 0;
    memset(&disabled, 0, sizeof(disabled));
    memset(&restricting, 0, sizeof(restricting));
    if (restricted_token == NULL
        || F3_BROKER_RESTRICTING_SID[0] == L'\0'
        || F3_BROKER_WORKER_SID[0] == L'\0'
        || wcscmp(
            F3_BROKER_RESTRICTING_SID,
            F3_BROKER_WORKER_SID
        ) == 0) {
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
        )
        || !ConvertStringSidToSidW(
            F3_BROKER_WORKER_SID,
            &worker_sid
        )) {
        goto cleanup;
    }
    disabled.Sid = disabled_sid;
    restricting.Sid = worker_sid;
    if (!CreateRestrictedToken(
            primary,
            DISABLE_MAX_PRIVILEGE,
            1,
            &disabled,
            0,
            NULL,
            1,
            &restricting,
            restricted_token
        )) {
        goto cleanup;
    }
    SetLastError(ERROR_SUCCESS);
    if (GetTokenInformation(
            *restricted_token,
            TokenRestrictedSids,
            NULL,
            0,
            &restricted_size
        )
        || GetLastError() != ERROR_INSUFFICIENT_BUFFER
        || restricted_size < sizeof(TOKEN_GROUPS)) {
        goto cleanup;
    }
    restricted = (TOKEN_GROUPS *)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        restricted_size
    );
    if (restricted == NULL
        || !GetTokenInformation(
            *restricted_token,
            TokenRestrictedSids,
            restricted,
            restricted_size,
            &restricted_size
        )
        || restricted->GroupCount != 1
        || !EqualSid(restricted->Groups[0].Sid, worker_sid)) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (restricted != NULL) {
        SecureZeroMemory(restricted, restricted_size);
        HeapFree(GetProcessHeap(), 0, restricted);
    }
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
    if (worker_sid != NULL) {
        LocalFree(worker_sid);
    }
    return ok;
}

static int restricted_token_cannot_read_path(
    HANDLE restricted_token,
    const wchar_t *path
) {
    HANDLE opened = INVALID_HANDLE_VALUE;
    DWORD open_error = ERROR_SUCCESS;
    int impersonating = 0;
    int ok = 0;
    if (restricted_token == NULL
        || path == NULL
        || !ImpersonateLoggedOnUser(restricted_token)) {
        goto cleanup;
    }
    impersonating = 1;
    opened = CreateFileW(
        path,
        GENERIC_READ,
        FILE_SHARE_READ,
        NULL,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
        NULL
    );
    open_error = GetLastError();
    if (opened != INVALID_HANDLE_VALUE) {
        goto cleanup;
    }
    ok = open_error == ERROR_ACCESS_DENIED;

cleanup:
    if (opened != INVALID_HANDLE_VALUE) {
        CloseHandle(opened);
    }
    if (impersonating && !RevertToSelf()) {
        ok = 0;
    }
    return ok;
}

static int restricted_token_cannot_mutate_completion_namespace(
    HANDLE restricted_token,
    const HeldDirectoryChain *chain
) {
    static const DWORD mutation_rights[] = {
        FILE_ADD_FILE,
        FILE_ADD_SUBDIRECTORY,
        FILE_DELETE_CHILD,
        FILE_WRITE_EA,
        FILE_WRITE_ATTRIBUTES,
        DELETE,
        WRITE_DAC,
        WRITE_OWNER,
    };
    GENERIC_MAPPING mapping = {
        FILE_GENERIC_READ,
        FILE_GENERIC_WRITE,
        FILE_GENERIC_EXECUTE,
        FILE_ALL_ACCESS
    };
    BYTE privilege_buffer[4096];
    PRIVILEGE_SET *privileges = (PRIVILEGE_SET *)privilege_buffer;
    HANDLE impersonation_token = NULL;
    size_t index;
    int ok = 0;
    if (restricted_token == NULL
        || chain == NULL
        || chain->count == 0
        || !DuplicateToken(
            restricted_token,
            SecurityImpersonation,
            &impersonation_token
        )) {
        goto cleanup;
    }
    for (index = 0; index < chain->count; ++index) {
        PSECURITY_DESCRIPTOR descriptor = NULL;
        PACL dacl = NULL;
        PSID owner = NULL;
        size_t right_index;
        HANDLE directory = chain->entries[index].handle;
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
            || owner == NULL
            || dacl == NULL) {
            if (descriptor != NULL) {
                LocalFree(descriptor);
            }
            goto cleanup;
        }
        for (
            right_index = 0;
            right_index
                < sizeof(mutation_rights) / sizeof(mutation_rights[0]);
            ++right_index
        ) {
            DWORD privilege_size = sizeof(privilege_buffer);
            DWORD granted = 0;
            DWORD desired = mutation_rights[right_index];
            BOOL access_status = FALSE;
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
                || access_status) {
                LocalFree(descriptor);
                goto cleanup;
            }
            SecureZeroMemory(
                privilege_buffer,
                sizeof(privilege_buffer)
            );
        }
        LocalFree(descriptor);
    }
    ok = 1;

cleanup:
    if (impersonation_token != NULL) {
        CloseHandle(impersonation_token);
    }
    SecureZeroMemory(privilege_buffer, sizeof(privilege_buffer));
    return ok;
}

#ifdef F3_BROKER_TESTING
static int create_test_supervisor_compatibility_token(
    HANDLE *restricted_token
) {
    HANDLE primary = NULL;
    PSID disabled_sid = NULL;
    SID_AND_ATTRIBUTES disabled;
    int ok = 0;
    memset(&disabled, 0, sizeof(disabled));
    if (restricted_token == NULL
        || F3_BROKER_RESTRICTING_SID[0] == L'\0'
        || !OpenProcessToken(
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
#endif

static int read_exact_pipe_frame(
    HANDLE pipe,
    HANDLE process,
    unsigned char *output,
    DWORD expected_size,
    DWORD timeout_ms
) {
    DWORD total = 0;
    DWORD available = 0;
    DWORD count = 0;
    DWORD elapsed = 0;
    DWORD exit_code = STILL_ACTIVE;
    if (pipe == INVALID_HANDLE_VALUE
        || process == NULL
        || output == NULL
        || expected_size == 0) {
        F3_PIPE_STAGE(1);
        return 0;
    }
    while (total < expected_size && elapsed < timeout_ms) {
        if (!PeekNamedPipe(pipe, NULL, 0, NULL, &available, NULL)) {
            F3_PIPE_STAGE(2);
            return 0;
        }
        if (available == 0) {
            if (!GetExitCodeProcess(process, &exit_code)
                || exit_code != STILL_ACTIVE) {
                F3_PIPE_STAGE(3);
                return 0;
            }
            Sleep(10);
            elapsed += 10;
            continue;
        }
        if (available > expected_size - total) {
            F3_PIPE_STAGE(4);
            return 0;
        }
        if (!ReadFile(
                pipe,
                output + total,
                available,
                &count,
                NULL
            )
            || count == 0) {
            F3_PIPE_STAGE(5);
            return 0;
        }
        total += count;
    }
    if (total != expected_size
        || !PeekNamedPipe(pipe, NULL, 0, NULL, &available, NULL)
        || available != 0) {
        F3_PIPE_STAGE(6);
        return 0;
    }
    return 1;
}

static int read_production_ready(
    HANDLE pipe,
    HANDLE process,
    const char authorization_sha256[65],
    const char *action
) {
    unsigned char frame[512];
    char expected[512];
    int length = snprintf(
        expected,
        sizeof(expected),
        "READY factor-v3-formal-native-broker-supervisor/v1\n"
        "launch_authorization_sha256=%s\n"
        "action=%s\n",
        authorization_sha256,
        action
    );
    int ok = length > 0
        && (size_t)length < sizeof(expected)
        && read_exact_pipe_frame(
            pipe,
            process,
            frame,
            (DWORD)length,
            120000
        )
        && memcmp(frame, expected, (size_t)length) == 0;
    SecureZeroMemory(frame, sizeof(frame));
    SecureZeroMemory(expected, sizeof(expected));
    return ok;
}

static int exact_hash_field(
    const unsigned char *frame,
    size_t frame_size,
    size_t *offset,
    const char *label,
    char output[65]
) {
    size_t label_size = strlen(label);
    size_t index;
    if (*offset + label_size + 65 > frame_size
        || memcmp(frame + *offset, label, label_size) != 0) {
        return 0;
    }
    *offset += label_size;
    for (index = 0; index < 64; ++index) {
        unsigned char value = frame[*offset + index];
        if (!((value >= '0' && value <= '9')
                || (value >= 'a' && value <= 'f'))) {
            return 0;
        }
        output[index] = (char)value;
    }
    output[64] = '\0';
    *offset += 64;
    if (frame[*offset] != '\n') {
        SecureZeroMemory(output, 65);
        return 0;
    }
    ++*offset;
    return 1;
}

static int read_production_completed(
    HANDLE pipe,
    HANDLE process,
    const char authorization_sha256[65],
    unsigned char *frame,
    DWORD *frame_size,
    char claim_sha256[65],
    char supervisor_completed_sha256[65],
    char worker_terminal_sha256[65]
) {
    static const char prefix[] =
        "COMPLETED factor-v3-formal-native-broker-supervisor/v1\n";
    static const char launch_label[] = "launch_authorization_sha256=";
    static const char claim_label[] = "claim_sha256=";
    static const char supervisor_label[] = "supervisor_completed_sha256=";
    static const char worker_label[] = "worker_terminal_sha256=";
    DWORD expected_size = (DWORD)(
        sizeof(prefix) - 1
        + sizeof(launch_label) - 1 + 65
        + sizeof(claim_label) - 1 + 65
        + sizeof(supervisor_label) - 1 + 65
        + sizeof(worker_label) - 1 + 65
    );
    char launch_sha256[65];
    size_t offset = 0;
    int ok = expected_size <= 1024
        && read_exact_pipe_frame(
            pipe,
            process,
            frame,
            expected_size,
            180000
        )
        && memcmp(frame, prefix, sizeof(prefix) - 1) == 0;
    if (!ok) {
        goto cleanup;
    }
    offset = sizeof(prefix) - 1;
    ok = exact_hash_field(
            frame,
            expected_size,
            &offset,
            launch_label,
            launch_sha256
        )
        && strcmp(launch_sha256, authorization_sha256) == 0
        && exact_hash_field(
            frame,
            expected_size,
            &offset,
            claim_label,
            claim_sha256
        )
        && exact_hash_field(
            frame,
            expected_size,
            &offset,
            supervisor_label,
            supervisor_completed_sha256
        )
        && exact_hash_field(
            frame,
            expected_size,
            &offset,
            worker_label,
            worker_terminal_sha256
        )
        && offset == expected_size;
    if (ok) {
        *frame_size = expected_size;
    }

cleanup:
    SecureZeroMemory(launch_sha256, sizeof(launch_sha256));
    if (!ok) {
        SecureZeroMemory(frame, 1024);
        SecureZeroMemory(claim_sha256, 65);
        SecureZeroMemory(supervisor_completed_sha256, 65);
        SecureZeroMemory(worker_terminal_sha256, 65);
    }
    return ok;
}

#define F3_PERSISTENT_COMPLETION_SCHEMA \
    "factor-v3-formal-native-broker-completed/v4"

static int atomic_write_persistent_completion(
    ProtectedCompletionNamespace *completion,
    const wchar_t *expected_path,
    const unsigned char *data,
    DWORD size
) {
    BY_HANDLE_FILE_INFORMATION before;
    BY_HANDLE_FILE_INFORMATION after;
    FILE_ATTRIBUTE_TAG_INFO tag;
    FILE_DISPOSITION_INFO disposition = {TRUE};
    F3_FILE_RENAME_INFORMATION *rename = NULL;
    F3_UNICODE_STRING name;
    F3_OBJECT_ATTRIBUTES object_attributes;
    F3_IO_STATUS_BLOCK io_status;
    F3_NT_CREATE_FILE nt_create_file;
    F3_NT_SET_INFORMATION_FILE nt_set_information_file;
    F3_RTL_GET_LAST_NT_STATUS rtl_get_last_nt_status;
    F3_RTL_NT_STATUS_TO_DOS_ERROR rtl_nt_status_to_dos_error;
    HMODULE ntdll;
    HeldFile written_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    unsigned char expected_digest[32];
    unsigned char observed_digest[32];
    wchar_t temporary_name[128];
    wchar_t observed_path[32768];
    wchar_t normalized_path[32768];
    HANDLE handle = INVALID_HANDLE_VALUE;
    HANDLE parent_handle;
    HANDLE temporary_parent_handle;
    DWORD handle_flags = HANDLE_FLAG_INHERIT;
    DWORD written = 0;
    DWORD total = 0;
    DWORD observed_length;
    DWORD failure_error = ERROR_SUCCESS;
    F3_NTSTATUS failure_ntstatus = 0;
    size_t final_length;
    size_t rename_size;
    int length;
    int ok = 0;
    memset(&before, 0, sizeof(before));
    memset(&after, 0, sizeof(after));
    memset(&tag, 0, sizeof(tag));
    memset(&name, 0, sizeof(name));
    memset(&object_attributes, 0, sizeof(object_attributes));
    memset(&io_status, 0, sizeof(io_status));
    if (completion == NULL
        || completion->chain.count == 0
        || expected_path == NULL
        || data == NULL
        || size == 0
        || !strict_windows_candidate_path(expected_path)
        || !held_directory_chain_unchanged(&completion->chain)
        || !hash_memory(data, size, expected_digest)) {
        F3_PRODUCTION_LAUNCH_STAGE(93301);
        return 0;
    }
    parent_handle = completion->chain.entries[
        completion->chain.count - 1
    ].handle;
    if (completion->chain.count < 2
        || parent_handle == INVALID_HANDLE_VALUE
        || wcslen(completion->final_name) != 69
        || wcscmp(completion->final_name + 64, L".json") != 0) {
        F3_PRODUCTION_LAUNCH_STAGE(93302);
        return 0;
    }
    temporary_parent_handle = completion->chain.entries[
        completion->chain.count - 2
    ].handle;
    if (temporary_parent_handle == INVALID_HANDLE_VALUE) {
        F3_PRODUCTION_LAUNCH_STAGE(93302);
        return 0;
    }
    ntdll = GetModuleHandleW(L"ntdll.dll");
    nt_create_file = ntdll == NULL
        ? NULL
        : (F3_NT_CREATE_FILE)(void *)GetProcAddress(
            ntdll,
            "NtCreateFile"
        );
    nt_set_information_file = ntdll == NULL
        ? NULL
        : (F3_NT_SET_INFORMATION_FILE)(void *)GetProcAddress(
            ntdll,
            "NtSetInformationFile"
        );
    rtl_get_last_nt_status = ntdll == NULL
        ? NULL
        : (F3_RTL_GET_LAST_NT_STATUS)(void *)GetProcAddress(
            ntdll,
            "RtlGetLastNtStatus"
        );
    rtl_nt_status_to_dos_error = ntdll == NULL
        ? NULL
        : (F3_RTL_NT_STATUS_TO_DOS_ERROR)(void *)GetProcAddress(
            ntdll,
            "RtlNtStatusToDosError"
        );
    if (nt_create_file == NULL
        || nt_set_information_file == NULL
        || rtl_get_last_nt_status == NULL
        || rtl_nt_status_to_dos_error == NULL) {
        F3_PRODUCTION_LAUNCH_STAGE(93303);
        return 0;
    }
    F3_PRODUCTION_LAUNCH_STAGE(9331);
    length = swprintf(
        temporary_name,
        sizeof(temporary_name) / sizeof(temporary_name[0]),
        L".%ls.tmp-%lu-%llu",
        completion->final_name,
        GetCurrentProcessId(),
        (unsigned long long)GetTickCount64()
    );
    if (length <= 0
        || (size_t)length
            >= sizeof(temporary_name) / sizeof(temporary_name[0])
        || (size_t)length > USHRT_MAX / sizeof(wchar_t)) {
        goto cleanup;
    }
    name.Length = (USHORT)((size_t)length * sizeof(wchar_t));
    name.MaximumLength = name.Length;
    name.Buffer = temporary_name;
    object_attributes.Length = sizeof(object_attributes);
    object_attributes.RootDirectory = temporary_parent_handle;
    object_attributes.ObjectName = &name;
    object_attributes.Attributes = F3_OBJ_CASE_INSENSITIVE;
    failure_ntstatus = nt_create_file(
            &handle,
            GENERIC_READ | GENERIC_WRITE | DELETE | SYNCHRONIZE,
            &object_attributes,
            &io_status,
            NULL,
            FILE_ATTRIBUTE_NORMAL,
            0,
            F3_FILE_CREATE,
            F3_FILE_NON_DIRECTORY_FILE
                | F3_FILE_SYNCHRONOUS_IO_NONALERT
                | F3_FILE_WRITE_THROUGH,
            NULL,
            0
        );
    if (failure_ntstatus < 0
        || handle == INVALID_HANDLE_VALUE
        || !GetHandleInformation(handle, &handle_flags)
        || (handle_flags & HANDLE_FLAG_INHERIT) != 0
        || !GetFileInformationByHandle(handle, &before)
        || before.nNumberOfLinks != 1
        || (before.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(9332);
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
    written_file.handle = handle;
    if (!hash_held_file(&written_file, observed_digest)
        || memcmp(expected_digest, observed_digest, 32) != 0) {
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(9333);
    final_length = wcslen(completion->final_name);
    if (final_length > (
            SIZE_MAX - offsetof(F3_FILE_RENAME_INFORMATION, FileName)
        )
            / sizeof(wchar_t)
        || final_length > UINT_MAX / sizeof(wchar_t)) {
        goto cleanup;
    }
    rename_size = offsetof(F3_FILE_RENAME_INFORMATION, FileName)
        + final_length * sizeof(wchar_t);
    if (rename_size > UINT_MAX) {
        goto cleanup;
    }
    rename = (F3_FILE_RENAME_INFORMATION *)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        rename_size
    );
    if (rename == NULL) {
        goto cleanup;
    }
    rename->ReplaceIfExists = FALSE;
    rename->RootDirectory = parent_handle;
    rename->FileNameLength = (DWORD)(final_length * sizeof(wchar_t));
    memcpy(
        rename->FileName,
        completion->final_name,
        rename->FileNameLength
    );
    F3_PRODUCTION_LAUNCH_STAGE(93331);
    memset(&io_status, 0, sizeof(io_status));
    failure_ntstatus = nt_set_information_file(
        handle,
        &io_status,
        rename,
        (ULONG)rename_size,
        F3_FILE_RENAME_INFORMATION_CLASS
    );
    if (failure_ntstatus < 0) {
        failure_error = rtl_nt_status_to_dos_error(failure_ntstatus);
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(93332);
    if (!FlushFileBuffers(handle)) {
        failure_error = GetLastError();
        failure_ntstatus = rtl_get_last_nt_status();
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(93333);
    if (!GetFileInformationByHandle(handle, &after)
        || !GetFileInformationByHandleEx(
            handle,
            FileAttributeTagInfo,
            &tag,
            sizeof(tag)
        )
        || before.dwVolumeSerialNumber != after.dwVolumeSerialNumber
        || before.nFileIndexHigh != after.nFileIndexHigh
        || before.nFileIndexLow != after.nFileIndexLow
        || after.nNumberOfLinks != 1
        || after.nFileSizeHigh != 0
        || after.nFileSizeLow != size
        || (after.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0
        || (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
        failure_error = GetLastError();
        failure_ntstatus = rtl_get_last_nt_status();
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(93334);
    if (!held_file_allows_only_trusted_mutation(handle)) {
        failure_error = GetLastError();
        failure_ntstatus = rtl_get_last_nt_status();
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(93335);
    observed_length = GetFinalPathNameByHandleW(
        handle,
        observed_path,
        (DWORD)(sizeof(observed_path) / sizeof(observed_path[0])),
        FILE_NAME_NORMALIZED | VOLUME_NAME_DOS
    );
    if (observed_length == 0
        || observed_length
            >= sizeof(observed_path) / sizeof(observed_path[0])
        || !final_path_without_prefix(
            observed_path,
            normalized_path,
            sizeof(normalized_path) / sizeof(normalized_path[0])
        )
        || _wcsicmp(normalized_path, expected_path) != 0
        || !held_directory_chain_unchanged(&completion->chain)) {
        failure_error = GetLastError();
        failure_ntstatus = rtl_get_last_nt_status();
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(9334);
    ok = 1;

cleanup:
#ifdef F3_BROKER_TESTING
    f3_test_atomic_write_error = failure_error;
    f3_test_atomic_write_ntstatus = failure_ntstatus;
#endif
    written_file.handle = INVALID_HANDLE_VALUE;
    if (!ok && handle != INVALID_HANDLE_VALUE) {
        SetFileInformationByHandle(
            handle,
            FileDispositionInfo,
            &disposition,
            sizeof(disposition)
        );
    }
    if (handle != INVALID_HANDLE_VALUE) {
        CloseHandle(handle);
    }
    if (rename != NULL) {
        SecureZeroMemory(rename, rename_size);
        HeapFree(GetProcessHeap(), 0, rename);
    }
    SecureZeroMemory(&before, sizeof(before));
    SecureZeroMemory(&after, sizeof(after));
    SecureZeroMemory(&tag, sizeof(tag));
    SecureZeroMemory(&name, sizeof(name));
    SecureZeroMemory(&object_attributes, sizeof(object_attributes));
    SecureZeroMemory(&io_status, sizeof(io_status));
    SecureZeroMemory(expected_digest, sizeof(expected_digest));
    SecureZeroMemory(observed_digest, sizeof(observed_digest));
    SecureZeroMemory(temporary_name, sizeof(temporary_name));
    SecureZeroMemory(observed_path, sizeof(observed_path));
    SecureZeroMemory(normalized_path, sizeof(normalized_path));
    if (!ok && failure_error != ERROR_SUCCESS) {
        SetLastError(failure_error);
    }
    return ok;
}

static int delete_failed_persistent_completion(
    ProtectedCompletionNamespace *completion,
    const char expected_sha256[65]
) {
    FILE_ATTRIBUTE_TAG_INFO tag;
    FILE_DISPOSITION_INFO disposition = {TRUE};
    F3_UNICODE_STRING name;
    F3_OBJECT_ATTRIBUTES object_attributes;
    F3_IO_STATUS_BLOCK io_status;
    F3_NT_CREATE_FILE nt_create_file;
    HMODULE ntdll;
    HeldFile file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    BY_HANDLE_FILE_INFORMATION information;
    unsigned char digest[32];
    char observed_sha256[65];
    HANDLE parent_handle;
    int ok = 0;
    memset(&tag, 0, sizeof(tag));
    memset(&name, 0, sizeof(name));
    memset(&object_attributes, 0, sizeof(object_attributes));
    memset(&io_status, 0, sizeof(io_status));
    memset(&information, 0, sizeof(information));
    memset(digest, 0, sizeof(digest));
    memset(observed_sha256, 0, sizeof(observed_sha256));
    if (completion == NULL
        || completion->chain.count == 0
        || expected_sha256 == NULL
        || strlen(expected_sha256) != 64
        || !held_directory_chain_unchanged(&completion->chain)) {
        goto cleanup;
    }
    parent_handle = completion->chain.entries[
        completion->chain.count - 1
    ].handle;
    ntdll = GetModuleHandleW(L"ntdll.dll");
    nt_create_file = ntdll == NULL
        ? NULL
        : (F3_NT_CREATE_FILE)(void *)GetProcAddress(
            ntdll,
            "NtCreateFile"
        );
    if (parent_handle == INVALID_HANDLE_VALUE
        || nt_create_file == NULL
        || wcslen(completion->final_name) > USHRT_MAX / sizeof(wchar_t)) {
        goto cleanup;
    }
    name.Length = (USHORT)(
        wcslen(completion->final_name) * sizeof(wchar_t)
    );
    name.MaximumLength = name.Length;
    name.Buffer = completion->final_name;
    object_attributes.Length = sizeof(object_attributes);
    object_attributes.RootDirectory = parent_handle;
    object_attributes.ObjectName = &name;
    object_attributes.Attributes = F3_OBJ_CASE_INSENSITIVE;
    if (nt_create_file(
            &file.handle,
            GENERIC_READ | DELETE | SYNCHRONIZE,
            &object_attributes,
            &io_status,
            NULL,
            FILE_ATTRIBUTE_NORMAL,
            0,
            F3_FILE_OPEN,
            F3_FILE_NON_DIRECTORY_FILE
                | F3_FILE_SYNCHRONOUS_IO_NONALERT
                | F3_FILE_OPEN_REPARSE_POINT,
            NULL,
            0
        ) < 0
        || file.handle == INVALID_HANDLE_VALUE
        || !GetFileInformationByHandle(file.handle, &information)
        || !GetFileInformationByHandleEx(
            file.handle,
            FileAttributeTagInfo,
            &tag,
            sizeof(tag)
        )
        || information.nNumberOfLinks != 1
        || information.nFileSizeHigh != 0
        || (information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0
        || (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0
        || !held_file_allows_only_trusted_mutation(file.handle)) {
        goto cleanup;
    }
    file.size_high = information.nFileSizeHigh;
    file.size_low = information.nFileSizeLow;
    if (!hash_held_file(&file, digest)) {
        goto cleanup;
    }
    digest_to_ascii(digest, observed_sha256);
    if (strcmp(observed_sha256, expected_sha256) != 0
        || !SetFileInformationByHandle(
            file.handle,
            FileDispositionInfo,
            &disposition,
            sizeof(disposition)
        )) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    close_held(&file);
    if (ok && !held_directory_chain_unchanged(&completion->chain)) {
        ok = 0;
    }
    SecureZeroMemory(&tag, sizeof(tag));
    SecureZeroMemory(&name, sizeof(name));
    SecureZeroMemory(&object_attributes, sizeof(object_attributes));
    SecureZeroMemory(&io_status, sizeof(io_status));
    SecureZeroMemory(&information, sizeof(information));
    SecureZeroMemory(digest, sizeof(digest));
    SecureZeroMemory(observed_sha256, sizeof(observed_sha256));
    return ok;
}

static int fixed_completion_token(const char *value) {
    size_t index;
    size_t length;
    if (value == NULL || value[0] == '\0') {
        return 0;
    }
    length = strlen(value);
    if (length > 128) {
        return 0;
    }
    for (index = 0; index < length; ++index) {
        unsigned char character = (unsigned char)value[index];
        if (!((character >= 'a' && character <= 'z')
                || (character >= 'A' && character <= 'Z')
                || (character >= '0' && character <= '9')
                || character == '-'
                || character == '.'
                || character == '_'
                || character == '/')) {
            return 0;
        }
    }
    return 1;
}

static int manifest_completion_public_blob(
    unsigned char **blob,
    DWORD *blob_size
) {
    const char *hex = F3_BROKER_COMPLETION_PUBLIC_BLOB_HEX;
    size_t hex_size = strlen(hex);
    unsigned char *decoded = NULL;
    BCRYPT_RSAKEY_BLOB *header;
    unsigned char digest[32];
    char observed_sha256[65];
    size_t index;
    int ok = 0;
    memset(digest, 0, sizeof(digest));
    memset(observed_sha256, 0, sizeof(observed_sha256));
    if (blob == NULL
        || blob_size == NULL
        || !fixed_completion_token(F3_BROKER_COMPLETION_KEY_ID)
        || !fixed_completion_token(F3_BROKER_COMPLETION_KEY_VERSION)
        || strlen(F3_BROKER_COMPLETION_PUBLIC_BLOB_SHA256) != 64
        || hex_size == 0
        || hex_size % 2 != 0
        || hex_size / 2 > UINT_MAX
        || hex_size / 2 < sizeof(BCRYPT_RSAKEY_BLOB)) {
        goto cleanup;
    }
    decoded = (unsigned char *)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        hex_size / 2
    );
    if (decoded == NULL) {
        goto cleanup;
    }
    for (index = 0; index < hex_size; index += 2) {
        int high = hex_value(hex[index]);
        int low = hex_value(hex[index + 1]);
        if (high < 0 || low < 0) {
            goto cleanup;
        }
        decoded[index / 2] = (unsigned char)((high << 4) | low);
    }
    header = (BCRYPT_RSAKEY_BLOB *)decoded;
    if (header->Magic != BCRYPT_RSAPUBLIC_MAGIC
        || header->BitLength != F3_EXECUTION_RSA_BITS
        || header->cbPrime1 != 0
        || header->cbPrime2 != 0
        || header->cbPublicExp == 0
        || header->cbModulus != F3_EXECUTION_RSA_BITS / 8
        || sizeof(BCRYPT_RSAKEY_BLOB)
            + (size_t)header->cbPublicExp
            + (size_t)header->cbModulus
            != hex_size / 2
        || !hash_memory(
            decoded,
            (DWORD)(hex_size / 2),
            digest
        )) {
        goto cleanup;
    }
    digest_to_ascii(digest, observed_sha256);
    if (strcmp(
            observed_sha256,
            F3_BROKER_COMPLETION_PUBLIC_BLOB_SHA256
        ) != 0) {
        goto cleanup;
    }
    *blob = decoded;
    *blob_size = (DWORD)(hex_size / 2);
    decoded = NULL;
    ok = 1;

cleanup:
    if (decoded != NULL) {
        SecureZeroMemory(decoded, hex_size / 2);
        HeapFree(GetProcessHeap(), 0, decoded);
    }
    SecureZeroMemory(digest, sizeof(digest));
    SecureZeroMemory(observed_sha256, sizeof(observed_sha256));
    return ok;
}

static int import_completion_public_key(
    BCRYPT_ALG_HANDLE *algorithm,
    BCRYPT_KEY_HANDLE *key
) {
    unsigned char *blob = NULL;
    DWORD blob_size = 0;
    int ok = algorithm != NULL
        && key != NULL
        && manifest_completion_public_blob(&blob, &blob_size)
        && BCryptOpenAlgorithmProvider(
            algorithm,
            BCRYPT_RSA_ALGORITHM,
            NULL,
            0
        ) >= 0
        && BCryptImportKeyPair(
            *algorithm,
            NULL,
            BCRYPT_RSAPUBLIC_BLOB,
            key,
            blob,
            blob_size,
            0
        ) >= 0;
    if (blob != NULL) {
        SecureZeroMemory(blob, blob_size);
        HeapFree(GetProcessHeap(), 0, blob);
    }
    if (!ok) {
        if (key != NULL && *key != NULL) {
            BCryptDestroyKey(*key);
            *key = NULL;
        }
        if (algorithm != NULL && *algorithm != NULL) {
            BCryptCloseAlgorithmProvider(*algorithm, 0);
            *algorithm = NULL;
        }
    }
    return ok;
}

static int persistent_cng_key_matches_public_pin(NCRYPT_KEY_HANDLE key) {
    unsigned char *expected = NULL;
    unsigned char *observed = NULL;
    DWORD expected_size = 0;
    DWORD observed_size = 0;
    int ok = key != 0
        && manifest_completion_public_blob(&expected, &expected_size)
        && NCryptExportKey(
            key,
            0,
            BCRYPT_RSAPUBLIC_BLOB,
            NULL,
            NULL,
            0,
            &observed_size,
            0
        ) == ERROR_SUCCESS
        && observed_size == expected_size;
    if (ok) {
        observed = (unsigned char *)HeapAlloc(
            GetProcessHeap(),
            HEAP_ZERO_MEMORY,
            observed_size
        );
        ok = observed != NULL
            && NCryptExportKey(
                key,
                0,
                BCRYPT_RSAPUBLIC_BLOB,
                NULL,
                observed,
                observed_size,
                &observed_size,
                0
            ) == ERROR_SUCCESS
            && observed_size == expected_size
            && memcmp(observed, expected, expected_size) == 0;
    }
    if (observed != NULL) {
        SecureZeroMemory(observed, observed_size);
        HeapFree(GetProcessHeap(), 0, observed);
    }
    if (expected != NULL) {
        SecureZeroMemory(expected, expected_size);
        HeapFree(GetProcessHeap(), 0, expected);
    }
    return ok;
}

static int open_persistent_cng_signing_key(
    NCRYPT_PROV_HANDLE *provider,
    NCRYPT_KEY_HANDLE *key
) {
    DWORD export_policy = UINT_MAX;
    DWORD result_size = 0;
    DWORD private_size = 0;
    if (provider == NULL
        || key == NULL
        || F3_BROKER_CNG_KEY_NAME[0] == L'\0') {
        return 0;
    }
    *provider = 0;
    *key = 0;
    if (f3_broker_open_cng_provider(provider) != ERROR_SUCCESS
        || NCryptOpenKey(
            *provider,
            key,
            F3_BROKER_CNG_KEY_NAME,
            0,
            0
        ) != ERROR_SUCCESS
        || NCryptGetProperty(
            *key,
            NCRYPT_EXPORT_POLICY_PROPERTY,
            (PBYTE)&export_policy,
            sizeof(export_policy),
            &result_size,
            0
        ) != ERROR_SUCCESS
        || result_size != sizeof(export_policy)
        || export_policy != 0
        || NCryptExportKey(
            *key,
            0,
            BCRYPT_RSAFULLPRIVATE_BLOB,
            NULL,
            NULL,
            0,
            &private_size,
            0
        ) == ERROR_SUCCESS) {
        return 0;
    }
    return persistent_cng_key_matches_public_pin(*key);
}

static int validate_completion_lineage_before_signing(
    const ProductionCandidate *candidate,
    const SignedEnvelope *launch_envelope,
    const char launch_authorization_sha256[65],
    const char claim_sha256[65],
    const char supervisor_completed_sha256[65],
    const char worker_terminal_sha256[65],
    DWORD *worker_terminal_bytes,
    HeldFile *claim_file,
    HeldFile *completed_file,
    HeldFile *worker_terminal_file
) {
    static const char *const claim_keys[] = {
        "action",
        "authorization_id_sha256",
        "authorization_nonce_sha256",
        "bootstrap_execution_authorization_sha256",
        "launch_authorization_sha256",
        "replay_scope",
        "schema",
        "status",
    };
    static const char *const completed_keys[] = {
        "artifact_manifest_sha256",
        "claim_sha256",
        "launch_authorization_sha256",
        "schema",
        "status",
        "worker_terminal_bytes",
        "worker_terminal_schema",
        "worker_terminal_sha256",
    };
    static const char *const worker_terminal_keys[] = {
        "artifacts",
        "authorization_nonce_sha256",
        "bootstrap_execution_authorization_sha256",
        "launch_action",
        "launch_authorization_sha256",
        "result",
        "schema",
        "status",
        "stdlib_inventory_root_sha256",
        "worker_action",
    };
    SignedEnvelope claim_view;
    SignedEnvelope worker_terminal_view;
    unsigned char *claim_raw = NULL;
    unsigned char *completed_raw = NULL;
    unsigned char *worker_terminal_raw = NULL;
    DWORD claim_size = 0;
    DWORD completed_size = 0;
    DWORD worker_terminal_size = 0;
    DWORD completed_worker_terminal_bytes = 0;
    unsigned char digest[32];
    char observed_sha256[65];
    wchar_t claim_path[32768];
    wchar_t completed_path[32768];
    wchar_t worker_terminal_path[32768];
    int ok = 0;
    memset(&claim_view, 0, sizeof(claim_view));
    memset(&worker_terminal_view, 0, sizeof(worker_terminal_view));
    memset(digest, 0, sizeof(digest));
    memset(observed_sha256, 0, sizeof(observed_sha256));
    memset(claim_path, 0, sizeof(claim_path));
    memset(completed_path, 0, sizeof(completed_path));
    memset(worker_terminal_path, 0, sizeof(worker_terminal_path));
    if (candidate == NULL
        || launch_envelope == NULL
        || worker_terminal_bytes == NULL
        || claim_file == NULL
        || completed_file == NULL
        || worker_terminal_file == NULL
        || claim_file->handle != INVALID_HANDLE_VALUE
        || completed_file->handle != INVALID_HANDLE_VALUE
        || worker_terminal_file->handle != INVALID_HANDLE_VALUE
        || !expected_ledger_path(
            candidate->execution_ledger_root,
            "claims",
            launch_authorization_sha256,
            claim_path,
            sizeof(claim_path) / sizeof(claim_path[0])
        )
        || !open_held_file(
            claim_path,
            F3_MAX_AUTHORIZATION_BYTES,
            claim_file
        )
        || !hash_held_file(claim_file, digest)) {
        goto cleanup;
    }
    digest_to_ascii(digest, observed_sha256);
    if (strcmp(observed_sha256, claim_sha256) != 0
        || !read_candidate(claim_file, &claim_raw, &claim_size)) {
        goto cleanup;
    }
    claim_view.payload = claim_raw;
    claim_view.payload_size = claim_size;
    if (!json_top_has_exact_keys(
            claim_raw,
            claim_size,
            claim_keys,
            sizeof(claim_keys) / sizeof(claim_keys[0])
        )
        || !json_top_string_matches(
            claim_raw,
            claim_size,
            "schema",
            "factor-v3-formal-supervisor-execution-claim/v1",
            strlen("factor-v3-formal-supervisor-execution-claim/v1")
        )
        || !json_top_string_matches(
            claim_raw,
            claim_size,
            "status",
            "claimed",
            strlen("claimed")
        )
        || !json_top_string_matches(
            claim_raw,
            claim_size,
            "launch_authorization_sha256",
            launch_authorization_sha256,
            64
        )
        || !json_top_strings_equal(
            &claim_view,
            "action",
            launch_envelope,
            "action"
        )
        || !json_top_strings_equal(
            &claim_view,
            "authorization_id_sha256",
            launch_envelope,
            "authorization_id_sha256"
        )
        || !json_top_strings_equal(
            &claim_view,
            "authorization_nonce_sha256",
            launch_envelope,
            "authorization_nonce_sha256"
        )
        || !json_top_strings_equal(
            &claim_view,
            "bootstrap_execution_authorization_sha256",
            launch_envelope,
            "bootstrap_execution_authorization_sha256"
        )
        || !json_top_strings_equal(
            &claim_view,
            "replay_scope",
            launch_envelope,
            "replay_scope"
        )
        || !expected_ledger_path(
            candidate->execution_ledger_root,
            "completed",
            launch_authorization_sha256,
            completed_path,
            sizeof(completed_path) / sizeof(completed_path[0])
        )
        || !open_held_file(
            completed_path,
            F3_MAX_AUTHORIZATION_BYTES,
            completed_file
        )
        || !hash_held_file(completed_file, digest)) {
        goto cleanup;
    }
    digest_to_ascii(digest, observed_sha256);
    if (strcmp(observed_sha256, supervisor_completed_sha256) != 0
        || !read_candidate(
            completed_file,
            &completed_raw,
            &completed_size
        )
        || !json_top_has_exact_keys(
            completed_raw,
            completed_size,
            completed_keys,
            sizeof(completed_keys) / sizeof(completed_keys[0])
        )
        || !json_top_string_matches(
            completed_raw,
            completed_size,
            "schema",
            "factor-v3-formal-supervisor-execution-completed/v2",
            strlen("factor-v3-formal-supervisor-execution-completed/v2")
        )
        || !json_top_string_matches(
            completed_raw,
            completed_size,
            "status",
            "completed",
            strlen("completed")
        )
        || !json_top_string_matches(
            completed_raw,
            completed_size,
            "launch_authorization_sha256",
            launch_authorization_sha256,
            64
        )
        || !json_top_string_matches(
            completed_raw,
            completed_size,
            "claim_sha256",
            claim_sha256,
            64
        )
        || !json_top_string_matches(
            completed_raw,
            completed_size,
            "worker_terminal_schema",
            "factor-v3-formal-bootstrap-worker-terminal/v2",
            strlen("factor-v3-formal-bootstrap-worker-terminal/v2")
        )
        || !json_top_uint32(
            completed_raw,
            completed_size,
            "worker_terminal_bytes",
            &completed_worker_terminal_bytes
        )
        || completed_worker_terminal_bytes == 0
        || completed_worker_terminal_bytes > 1024 * 1024
        || !json_top_string_matches(
            completed_raw,
            completed_size,
            "worker_terminal_sha256",
            worker_terminal_sha256,
            64
        )
        || !json_top_has_sha256(
            completed_raw,
            completed_size,
            "artifact_manifest_sha256"
        )
        || !expected_ledger_path(
            candidate->execution_ledger_root,
            "worker_terminals",
            launch_authorization_sha256,
            worker_terminal_path,
            sizeof(worker_terminal_path) / sizeof(worker_terminal_path[0])
        )
        || !open_held_file(
            worker_terminal_path,
            1024 * 1024,
            worker_terminal_file
        )
        || worker_terminal_file->size_high != 0
        || worker_terminal_file->size_low != completed_worker_terminal_bytes
        || !hash_held_file(worker_terminal_file, digest)) {
        goto cleanup;
    }
    digest_to_ascii(digest, observed_sha256);
    if (strcmp(observed_sha256, worker_terminal_sha256) != 0
        || !read_candidate(
            worker_terminal_file,
            &worker_terminal_raw,
            &worker_terminal_size
        )
        || worker_terminal_size != completed_worker_terminal_bytes
        || worker_terminal_size < 2
        || worker_terminal_raw[worker_terminal_size - 1] != '\n'
        || memchr(
            worker_terminal_raw,
            '\n',
            worker_terminal_size - 1
        ) != NULL) {
        goto cleanup;
    }
    worker_terminal_view.payload = worker_terminal_raw;
    worker_terminal_view.payload_size = worker_terminal_size - 1;
    if (!json_top_has_exact_keys(
            worker_terminal_raw,
            worker_terminal_size - 1,
            worker_terminal_keys,
            sizeof(worker_terminal_keys) / sizeof(worker_terminal_keys[0])
        )
        || !json_top_string_matches(
            worker_terminal_raw,
            worker_terminal_size - 1,
            "schema",
            "factor-v3-formal-bootstrap-worker-terminal/v2",
            strlen("factor-v3-formal-bootstrap-worker-terminal/v2")
        )
        || !json_top_string_matches(
            worker_terminal_raw,
            worker_terminal_size - 1,
            "status",
            "completed",
            strlen("completed")
        )
        || !json_top_string_matches(
            worker_terminal_raw,
            worker_terminal_size - 1,
            "launch_authorization_sha256",
            launch_authorization_sha256,
            64
        )
        || !json_top_string_matches(
            worker_terminal_raw,
            worker_terminal_size - 1,
            "worker_action",
            "run",
            strlen("run")
        )
        || !json_top_strings_equal(
            &worker_terminal_view,
            "launch_action",
            launch_envelope,
            "action"
        )
        || !json_top_strings_equal(
            &worker_terminal_view,
            "authorization_nonce_sha256",
            launch_envelope,
            "bootstrap_authorization_nonce_sha256"
        )
        || !json_top_strings_equal(
            &worker_terminal_view,
            "bootstrap_execution_authorization_sha256",
            launch_envelope,
            "bootstrap_execution_authorization_sha256"
        )
        || !json_top_strings_equal(
            &worker_terminal_view,
            "stdlib_inventory_root_sha256",
            launch_envelope,
            "stdlib_inventory_root_sha256"
        )
        || !held_unchanged(claim_file, NULL)
        || !held_unchanged(completed_file, NULL)
        || !held_unchanged(worker_terminal_file, NULL)) {
        goto cleanup;
    }
    *worker_terminal_bytes = completed_worker_terminal_bytes;
    ok = 1;

cleanup:
    if (worker_terminal_raw != NULL) {
        SecureZeroMemory(
            worker_terminal_raw,
            (SIZE_T)worker_terminal_size + 1
        );
        HeapFree(GetProcessHeap(), 0, worker_terminal_raw);
    }
    if (completed_raw != NULL) {
        SecureZeroMemory(completed_raw, (SIZE_T)completed_size + 1);
        HeapFree(GetProcessHeap(), 0, completed_raw);
    }
    if (claim_raw != NULL) {
        SecureZeroMemory(claim_raw, (SIZE_T)claim_size + 1);
        HeapFree(GetProcessHeap(), 0, claim_raw);
    }
    if (!ok) {
        if (worker_terminal_bytes != NULL) {
            *worker_terminal_bytes = 0;
        }
        close_held(worker_terminal_file);
        close_held(completed_file);
        close_held(claim_file);
    }
    SecureZeroMemory(&claim_view, sizeof(claim_view));
    SecureZeroMemory(
        &worker_terminal_view,
        sizeof(worker_terminal_view)
    );
    SecureZeroMemory(digest, sizeof(digest));
    SecureZeroMemory(observed_sha256, sizeof(observed_sha256));
    SecureZeroMemory(claim_path, sizeof(claim_path));
    SecureZeroMemory(completed_path, sizeof(completed_path));
    SecureZeroMemory(worker_terminal_path, sizeof(worker_terminal_path));
    return ok;
}

static int hash_completion_signature_payload(
    const unsigned char *payload,
    DWORD payload_size,
    unsigned char digest[32]
) {
    static const unsigned char domain[] =
        "factor-v3-formal-native-broker-completion-signature/v1";
    unsigned char *input = NULL;
    size_t domain_size = sizeof(domain) - 1;
    size_t input_size;
    int ok = 0;
    if (payload == NULL
        || payload_size == 0
        || digest == NULL
        || (size_t)payload_size > SIZE_MAX - domain_size - 1) {
        return 0;
    }
    input_size = domain_size + 1 + (size_t)payload_size;
    if (input_size > UINT_MAX) {
        return 0;
    }
    input = (unsigned char *)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        input_size
    );
    if (input == NULL) {
        return 0;
    }
    memcpy(input, domain, domain_size);
    input[domain_size] = '\0';
    memcpy(input + domain_size + 1, payload, payload_size);
    ok = hash_memory(input, (DWORD)input_size, digest);
    SecureZeroMemory(input, input_size);
    HeapFree(GetProcessHeap(), 0, input);
    return ok;
}

static int write_persistent_cng_completion(
    ProtectedCompletionNamespace *completion,
    const wchar_t *completed_path,
    const char launch_authorization_sha256[65],
    const char candidate_sha256[65],
    const char claim_sha256[65],
    const char supervisor_completed_sha256[65],
    const char worker_terminal_sha256[65],
    DWORD worker_terminal_bytes,
    char completed_receipt_sha256[65]
) {
    BCRYPT_PKCS1_PADDING_INFO padding = {BCRYPT_SHA256_ALGORITHM};
    NCRYPT_PROV_HANDLE provider = 0;
    NCRYPT_KEY_HANDLE key = 0;
    unsigned char unsigned_digest[32];
    unsigned char receipt_digest[32];
    unsigned char *signature = NULL;
    DWORD signature_size = 0;
    char payload[2048];
    unsigned char *receipt = NULL;
    size_t receipt_capacity = 0;
    size_t offset = 0;
    size_t index;
    int payload_length;
    int ok = 0;
    payload_length = snprintf(
        payload,
        sizeof(payload),
        "{"
        "\"candidate_sha256\":\"%s\","
        "\"claim_sha256\":\"%s\","
        "\"completion_key_id\":\"%s\","
        "\"completion_key_version\":\"%s\","
        "\"completion_public_blob_sha256\":\"%s\","
        "\"launch_authorization_sha256\":\"%s\","
        "\"schema\":\"" F3_PERSISTENT_COMPLETION_SCHEMA "\","
        "\"signature_algorithm\":\"RSA-PKCS1-SHA256\","
        "\"status\":\"completed\","
        "\"supervisor_completed_sha256\":\"%s\","
        "\"worker_terminal_bytes\":%lu,"
        "\"worker_terminal_schema\":"
        "\"factor-v3-formal-bootstrap-worker-terminal/v2\","
        "\"worker_terminal_sha256\":\"%s\""
        "}",
        candidate_sha256,
        claim_sha256,
        F3_BROKER_COMPLETION_KEY_ID,
        F3_BROKER_COMPLETION_KEY_VERSION,
        F3_BROKER_COMPLETION_PUBLIC_BLOB_SHA256,
        launch_authorization_sha256,
        supervisor_completed_sha256,
        (unsigned long)worker_terminal_bytes,
        worker_terminal_sha256
    );
    if (payload_length <= 0
        || (size_t)payload_length >= sizeof(payload)
        || completion == NULL
        || completed_path == NULL
        || worker_terminal_bytes == 0
        || worker_terminal_bytes > 1024 * 1024
        || !hash_completion_signature_payload(
            (const unsigned char *)payload,
            (DWORD)payload_length,
            unsigned_digest
        )) {
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(931);
    if (!open_persistent_cng_signing_key(&provider, &key)) {
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(932);
    if (f3_broker_sign_sha256_with_cng_key(
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
        ) != ERROR_SUCCESS) {
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(933);
    receipt_capacity = strlen("{\"payload\":")
        + (size_t)payload_length
        + strlen(",\"signature_hex\":\"\"}")
        + (size_t)signature_size * 2
        + 1;
    receipt = (unsigned char *)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        receipt_capacity
    );
    if (receipt == NULL) {
        goto cleanup;
    }
    memcpy(receipt, "{\"payload\":", strlen("{\"payload\":"));
    offset = strlen("{\"payload\":");
    memcpy(receipt + offset, payload, (size_t)payload_length);
    offset += (size_t)payload_length;
    memcpy(
        receipt + offset,
        ",\"signature_hex\":\"",
        strlen(",\"signature_hex\":\"")
    );
    offset += strlen(",\"signature_hex\":\"");
    for (index = 0; index < signature_size; ++index) {
        static const char digits[] = "0123456789abcdef";
        receipt[offset++] = (unsigned char)digits[signature[index] >> 4];
        receipt[offset++] = (unsigned char)digits[signature[index] & 15];
    }
    memcpy(receipt + offset, "\"}", strlen("\"}"));
    offset += strlen("\"}");
    if (offset > UINT_MAX
        || !hash_memory(receipt, (DWORD)offset, receipt_digest)
        || !atomic_write_persistent_completion(
            completion,
            completed_path,
            receipt,
            (DWORD)offset
        )) {
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(934);
    digest_to_ascii(receipt_digest, completed_receipt_sha256);
    ok = 1;

cleanup:
    if (signature != NULL) {
        SecureZeroMemory(signature, signature_size);
        HeapFree(GetProcessHeap(), 0, signature);
    }
    if (key != 0) {
        NCryptFreeObject(key);
    }
    if (provider != 0) {
        NCryptFreeObject(provider);
    }
    if (receipt != NULL) {
        SecureZeroMemory(receipt, receipt_capacity);
        HeapFree(GetProcessHeap(), 0, receipt);
    }
    SecureZeroMemory(unsigned_digest, sizeof(unsigned_digest));
    SecureZeroMemory(receipt_digest, sizeof(receipt_digest));
    SecureZeroMemory(payload, sizeof(payload));
    if (!ok) {
        SecureZeroMemory(completed_receipt_sha256, 65);
    }
    return ok;
}

static int hex_signature(
    const unsigned char *value,
    size_t length,
    unsigned char **signature,
    DWORD *signature_size
) {
    size_t index;
    unsigned char *decoded;
    if (value == NULL
        || length == 0
        || length % 2 != 0
        || length / 2 > UINT_MAX
        || signature == NULL
        || signature_size == NULL) {
        return 0;
    }
    decoded = (unsigned char *)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        length / 2
    );
    if (decoded == NULL) {
        return 0;
    }
    for (index = 0; index < length; ++index) {
        unsigned char digit;
        if (value[index] >= '0' && value[index] <= '9') {
            digit = (unsigned char)(value[index] - '0');
        } else if (value[index] >= 'a' && value[index] <= 'f') {
            digit = (unsigned char)(value[index] - 'a' + 10);
        } else {
            SecureZeroMemory(decoded, length / 2);
            HeapFree(GetProcessHeap(), 0, decoded);
            return 0;
        }
        if (index % 2 == 0) {
            decoded[index / 2] = (unsigned char)(digit << 4);
        } else {
            decoded[index / 2] |= digit;
        }
    }
    *signature = decoded;
    *signature_size = (DWORD)(length / 2);
    return 1;
}

static int verify_persistent_cng_completion(
    const wchar_t *candidate_path
) {
    static const char *const envelope_keys[] = {
        "payload",
        "signature_hex",
    };
    static const char *const payload_keys[] = {
        "candidate_sha256",
        "claim_sha256",
        "completion_key_id",
        "completion_key_version",
        "completion_public_blob_sha256",
        "launch_authorization_sha256",
        "schema",
        "signature_algorithm",
        "status",
        "supervisor_completed_sha256",
        "worker_terminal_bytes",
        "worker_terminal_schema",
        "worker_terminal_sha256",
    };
    BCRYPT_PKCS1_PADDING_INFO padding = {BCRYPT_SHA256_ALGORITHM};
    BCRYPT_ALG_HANDLE algorithm = NULL;
    BCRYPT_KEY_HANDLE key = NULL;
    HeldFile candidate_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile launch_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile receipt_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile claim_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile completed_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile worker_terminal_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    ProductionCandidate candidate;
    SignedEnvelope launch_envelope;
    ByteSlice payload;
    ByteSlice signature_value;
    unsigned char *candidate_raw = NULL;
    unsigned char *launch_raw = NULL;
    unsigned char *receipt_raw = NULL;
    unsigned char *signature = NULL;
    DWORD candidate_size = 0;
    DWORD launch_size = 0;
    DWORD receipt_size = 0;
    DWORD signature_size = 0;
    unsigned char candidate_digest[32];
    unsigned char launch_digest[32];
    unsigned char payload_digest[32];
    wchar_t launch_path[32768];
    wchar_t receipt_path[32768];
    char candidate_sha256[65];
    char launch_sha256[65];
    char claim_sha256[65];
    char supervisor_sha256[65];
    char worker_sha256[65];
    DWORD receipt_worker_terminal_bytes = 0;
    DWORD observed_worker_terminal_bytes = 0;
    size_t json_size;
    int ok = 0;
    memset(&candidate, 0, sizeof(candidate));
    memset(&launch_envelope, 0, sizeof(launch_envelope));
    memset(&payload, 0, sizeof(payload));
    memset(&signature_value, 0, sizeof(signature_value));
    memset(candidate_digest, 0, sizeof(candidate_digest));
    memset(launch_digest, 0, sizeof(launch_digest));
    memset(payload_digest, 0, sizeof(payload_digest));
    memset(launch_path, 0, sizeof(launch_path));
    memset(receipt_path, 0, sizeof(receipt_path));
    memset(candidate_sha256, 0, sizeof(candidate_sha256));
    memset(launch_sha256, 0, sizeof(launch_sha256));
    memset(claim_sha256, 0, sizeof(claim_sha256));
    memset(supervisor_sha256, 0, sizeof(supervisor_sha256));
    memset(worker_sha256, 0, sizeof(worker_sha256));
    if (!open_held_file(
            candidate_path,
            F3_MAX_CANDIDATE_BYTES,
            &candidate_file
        )
        || !hash_held_file(&candidate_file, candidate_digest)
        || !filename_matches_candidate_digest(
            candidate_path,
            candidate_digest
        )
        || !read_candidate(&candidate_file, &candidate_raw, &candidate_size)
        || !parse_production_candidate(
            candidate_raw,
            candidate_size,
            &candidate
        )
        || !byte_slice_to_wide(
            candidate.launch_authorization_path,
            launch_path,
            sizeof(launch_path) / sizeof(launch_path[0])
        )
        || !open_held_file(
            launch_path,
            F3_MAX_AUTHORIZATION_BYTES,
            &launch_file
        )
        || !hash_held_file(&launch_file, launch_digest)
        || !filename_matches_digest_suffix(
            launch_path,
            launch_digest,
            L".json"
        )
        || !read_candidate(&launch_file, &launch_raw, &launch_size)
        || !parse_signed_envelope(
            launch_raw,
            launch_size,
            &launch_envelope
        )
        || !validate_current_launch_payload(
            &launch_envelope,
            &candidate
        )) {
        goto cleanup;
    }
    digest_to_ascii(candidate_digest, candidate_sha256);
    digest_to_ascii(launch_digest, launch_sha256);
    if (!expected_ledger_path(
            candidate.execution_ledger_root,
            "native_completed",
            launch_sha256,
            receipt_path,
            sizeof(receipt_path) / sizeof(receipt_path[0])
        )
        || !open_held_file(receipt_path, 16 * 1024, &receipt_file)
        || !read_candidate(&receipt_file, &receipt_raw, &receipt_size)
        || receipt_size < 2) {
        goto cleanup;
    }
    json_size = (size_t)receipt_size;
    if (!json_top_has_exact_keys(
            receipt_raw,
            json_size,
            envelope_keys,
            sizeof(envelope_keys) / sizeof(envelope_keys[0])
        )
        || !json_top_field(
            receipt_raw,
            json_size,
            "payload",
            &payload
        )
        || !json_top_field(
            receipt_raw,
            json_size,
            "signature_hex",
            &signature_value
        )
        || !json_top_has_exact_keys(
            (const unsigned char *)payload.value,
            payload.length,
            payload_keys,
            sizeof(payload_keys) / sizeof(payload_keys[0])
        )
        || !json_top_string_matches(
            (const unsigned char *)payload.value,
            payload.length,
            "candidate_sha256",
            candidate_sha256,
            64
        )
        || !json_top_string_matches(
            (const unsigned char *)payload.value,
            payload.length,
            "launch_authorization_sha256",
            launch_sha256,
            64
        )
        || !json_top_string_matches(
            (const unsigned char *)payload.value,
            payload.length,
            "completion_key_id",
            F3_BROKER_COMPLETION_KEY_ID,
            strlen(F3_BROKER_COMPLETION_KEY_ID)
        )
        || !json_top_string_matches(
            (const unsigned char *)payload.value,
            payload.length,
            "completion_key_version",
            F3_BROKER_COMPLETION_KEY_VERSION,
            strlen(F3_BROKER_COMPLETION_KEY_VERSION)
        )
        || !json_top_string_matches(
            (const unsigned char *)payload.value,
            payload.length,
            "completion_public_blob_sha256",
            F3_BROKER_COMPLETION_PUBLIC_BLOB_SHA256,
            64
        )
        || !json_top_string_matches(
            (const unsigned char *)payload.value,
            payload.length,
            "schema",
            F3_PERSISTENT_COMPLETION_SCHEMA,
            strlen(F3_PERSISTENT_COMPLETION_SCHEMA)
        )
        || !json_top_string_matches(
            (const unsigned char *)payload.value,
            payload.length,
            "signature_algorithm",
            "RSA-PKCS1-SHA256",
            strlen("RSA-PKCS1-SHA256")
        )
        || !json_top_string_matches(
            (const unsigned char *)payload.value,
            payload.length,
            "status",
            "completed",
            strlen("completed")
        )
        || !json_top_copy_sha256(
            (const unsigned char *)payload.value,
            payload.length,
            "claim_sha256",
            claim_sha256
        )
        || !json_top_copy_sha256(
            (const unsigned char *)payload.value,
            payload.length,
            "supervisor_completed_sha256",
            supervisor_sha256
        )
        || !json_top_copy_sha256(
            (const unsigned char *)payload.value,
            payload.length,
            "worker_terminal_sha256",
            worker_sha256
        )
        || !json_top_uint32(
            (const unsigned char *)payload.value,
            payload.length,
            "worker_terminal_bytes",
            &receipt_worker_terminal_bytes
        )
        || !json_top_string_matches(
            (const unsigned char *)payload.value,
            payload.length,
            "worker_terminal_schema",
            "factor-v3-formal-bootstrap-worker-terminal/v2",
            strlen("factor-v3-formal-bootstrap-worker-terminal/v2")
        )
        || signature_value.length < 4
        || signature_value.value[0] != '"'
        || signature_value.value[signature_value.length - 1] != '"'
        || !hex_signature(
            (const unsigned char *)signature_value.value + 1,
            signature_value.length - 2,
            &signature,
            &signature_size
        )
        || signature_size != F3_EXECUTION_RSA_BITS / 8
        || payload.length > UINT_MAX
        || !hash_completion_signature_payload(
            (const unsigned char *)payload.value,
            (DWORD)payload.length,
            payload_digest
        )
        || !import_completion_public_key(&algorithm, &key)
        || BCryptVerifySignature(
            key,
            &padding,
            payload_digest,
            sizeof(payload_digest),
            signature,
            signature_size,
            BCRYPT_PAD_PKCS1
        ) < 0
        || !validate_completion_lineage_before_signing(
            &candidate,
            &launch_envelope,
            launch_sha256,
            claim_sha256,
            supervisor_sha256,
            worker_sha256,
            &observed_worker_terminal_bytes,
            &claim_file,
            &completed_file,
            &worker_terminal_file
        )
        || observed_worker_terminal_bytes != receipt_worker_terminal_bytes
        || !held_unchanged(&receipt_file, NULL)
        || !held_unchanged(&launch_file, NULL)
        || !held_unchanged(&candidate_file, NULL)) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (signature != NULL) {
        SecureZeroMemory(signature, signature_size);
        HeapFree(GetProcessHeap(), 0, signature);
    }
    if (key != NULL) {
        BCryptDestroyKey(key);
    }
    if (algorithm != NULL) {
        BCryptCloseAlgorithmProvider(algorithm, 0);
    }
    if (receipt_raw != NULL) {
        SecureZeroMemory(receipt_raw, (SIZE_T)receipt_size + 1);
        HeapFree(GetProcessHeap(), 0, receipt_raw);
    }
    if (launch_raw != NULL) {
        SecureZeroMemory(launch_raw, (SIZE_T)launch_size + 1);
        HeapFree(GetProcessHeap(), 0, launch_raw);
    }
    if (candidate_raw != NULL) {
        SecureZeroMemory(candidate_raw, (SIZE_T)candidate_size + 1);
        HeapFree(GetProcessHeap(), 0, candidate_raw);
    }
    close_held(&worker_terminal_file);
    close_held(&completed_file);
    close_held(&claim_file);
    close_held(&receipt_file);
    close_held(&launch_file);
    close_held(&candidate_file);
    SecureZeroMemory(&candidate, sizeof(candidate));
    SecureZeroMemory(&launch_envelope, sizeof(launch_envelope));
    SecureZeroMemory(&payload, sizeof(payload));
    SecureZeroMemory(&signature_value, sizeof(signature_value));
    SecureZeroMemory(candidate_digest, sizeof(candidate_digest));
    SecureZeroMemory(launch_digest, sizeof(launch_digest));
    SecureZeroMemory(payload_digest, sizeof(payload_digest));
    SecureZeroMemory(launch_path, sizeof(launch_path));
    SecureZeroMemory(receipt_path, sizeof(receipt_path));
    SecureZeroMemory(candidate_sha256, sizeof(candidate_sha256));
    SecureZeroMemory(launch_sha256, sizeof(launch_sha256));
    SecureZeroMemory(claim_sha256, sizeof(claim_sha256));
    SecureZeroMemory(supervisor_sha256, sizeof(supervisor_sha256));
    SecureZeroMemory(worker_sha256, sizeof(worker_sha256));
    return ok;
}

#if 0
static int legacy_verify_persistent_cng_completion(
    const wchar_t *candidate_path
) {
    static const char schema[] =
        "schema=" F3_PERSISTENT_COMPLETION_SCHEMA "\n"
        "status=completed\n";
    static const char launch_label[] = "launch_authorization_sha256=";
    static const char candidate_label[] = "candidate_sha256=";
    static const char claim_label[] = "claim_sha256=";
    static const char supervisor_label[] = "supervisor_completed_sha256=";
    static const char worker_label[] = "worker_terminal_sha256=";
    static const char algorithm[] =
        "signature_algorithm=RSA-PKCS1-SHA256\n";
    static const char signature_label[] = "signature_hex=";
    BCRYPT_PKCS1_PADDING_INFO padding = {BCRYPT_SHA256_ALGORITHM};
    HeldFile candidate_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile launch_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile receipt_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile claim_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile supervisor_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    ProductionCandidate candidate;
    SignedEnvelope envelope;
    unsigned char *candidate_raw = NULL;
    unsigned char *launch_raw = NULL;
    unsigned char *receipt_raw = NULL;
    unsigned char *supervisor_raw = NULL;
    DWORD candidate_size = 0;
    DWORD launch_size = 0;
    DWORD receipt_size = 0;
    DWORD supervisor_size = 0;
    unsigned char candidate_digest[32];
    unsigned char launch_digest[32];
    unsigned char unsigned_digest[32];
    unsigned char observed_digest[32];
    unsigned char *signature = NULL;
    DWORD signature_size = 0;
    NCRYPT_PROV_HANDLE provider = 0;
    NCRYPT_KEY_HANDLE key = 0;
    wchar_t launch_path[32768];
    wchar_t receipt_path[32768];
    wchar_t claim_path[32768];
    wchar_t supervisor_path[32768];
    char candidate_sha256[65];
    char launch_sha256[65];
    char observed_sha256[65];
    char receipt_launch_sha256[65];
    char receipt_candidate_sha256[65];
    char claim_sha256[65];
    char supervisor_sha256[65];
    char worker_sha256[65];
    size_t offset = 0;
    size_t unsigned_size = 0;
    size_t signature_hex_size = 0;
    int ok = 0;
    memset(&candidate, 0, sizeof(candidate));
    memset(&envelope, 0, sizeof(envelope));
    if (!open_held_file(
            candidate_path,
            F3_MAX_CANDIDATE_BYTES,
            &candidate_file
        )
        || !hash_held_file(&candidate_file, candidate_digest)
        || !filename_matches_candidate_digest(
            candidate_path,
            candidate_digest
        )
        || !read_candidate(&candidate_file, &candidate_raw, &candidate_size)
        || !parse_production_candidate(
            candidate_raw,
            candidate_size,
            &candidate
        )
        || !byte_slice_to_wide(
            candidate.launch_authorization_path,
            launch_path,
            32768
        )
        || !open_held_file(
            launch_path,
            F3_MAX_AUTHORIZATION_BYTES,
            &launch_file
        )
        || !hash_held_file(&launch_file, launch_digest)
        || !filename_matches_digest_suffix(
            launch_path,
            launch_digest,
            L".json"
        )
        || !read_candidate(&launch_file, &launch_raw, &launch_size)
        || !parse_signed_envelope(launch_raw, launch_size, &envelope)
        || !validate_current_launch_payload(&envelope, &candidate)) {
        goto cleanup;
    }
    digest_to_ascii(candidate_digest, candidate_sha256);
    digest_to_ascii(launch_digest, launch_sha256);
    if (!expected_ledger_path(
            candidate.execution_ledger_root,
            "native_completed",
            launch_sha256,
            receipt_path,
            32768
        )
        || !open_held_file(receipt_path, 16 * 1024, &receipt_file)
        || !read_candidate(
            &receipt_file,
            &receipt_raw,
            &receipt_size
        )
        || receipt_size <= sizeof(schema) - 1
        || memcmp(receipt_raw, schema, sizeof(schema) - 1) != 0) {
        goto cleanup;
    }
    offset = sizeof(schema) - 1;
    if (!exact_hash_field(
            receipt_raw,
            receipt_size,
            &offset,
            launch_label,
            receipt_launch_sha256
        )
        || strcmp(receipt_launch_sha256, launch_sha256) != 0
        || !exact_hash_field(
            receipt_raw,
            receipt_size,
            &offset,
            candidate_label,
            receipt_candidate_sha256
        )
        || strcmp(receipt_candidate_sha256, candidate_sha256) != 0
        || !exact_hash_field(
            receipt_raw,
            receipt_size,
            &offset,
            claim_label,
            claim_sha256
        )
        || !exact_hash_field(
            receipt_raw,
            receipt_size,
            &offset,
            supervisor_label,
            supervisor_sha256
        )
        || !exact_hash_field(
            receipt_raw,
            receipt_size,
            &offset,
            worker_label,
            worker_sha256
        )
        || offset + sizeof(algorithm) - 1 > receipt_size
        || memcmp(
            receipt_raw + offset,
            algorithm,
            sizeof(algorithm) - 1
        ) != 0) {
        goto cleanup;
    }
    offset += sizeof(algorithm) - 1;
    unsigned_size = offset;
    if (offset + sizeof(signature_label) - 1 + 2 > receipt_size
        || memcmp(
            receipt_raw + offset,
            signature_label,
            sizeof(signature_label) - 1
        ) != 0
        || receipt_raw[receipt_size - 1] != '\n') {
        goto cleanup;
    }
    offset += sizeof(signature_label) - 1;
    signature_hex_size = receipt_size - offset - 1;
    if (!hex_signature(
            receipt_raw + offset,
            signature_hex_size,
            &signature,
            &signature_size
        )
        || !hash_memory(
            receipt_raw,
            (DWORD)unsigned_size,
            unsigned_digest
        )
        || !open_persistent_cng_signing_key(&provider, &key)
        || NCryptVerifySignature(
            key,
            &padding,
            unsigned_digest,
            sizeof(unsigned_digest),
            signature,
            signature_size,
            NCRYPT_PAD_PKCS1_FLAG
        ) != ERROR_SUCCESS
        || !expected_ledger_path(
            candidate.execution_ledger_root,
            "claims",
            launch_sha256,
            claim_path,
            32768
        )
        || !open_held_file(
            claim_path,
            F3_MAX_AUTHORIZATION_BYTES,
            &claim_file
        )
        || !hash_held_file(&claim_file, observed_digest)) {
        goto cleanup;
    }
    digest_to_ascii(observed_digest, observed_sha256);
    if (strcmp(observed_sha256, claim_sha256) != 0
        || !expected_ledger_path(
            candidate.execution_ledger_root,
            "completed",
            launch_sha256,
            supervisor_path,
            32768
        )
        || !open_held_file(
            supervisor_path,
            F3_MAX_AUTHORIZATION_BYTES,
            &supervisor_file
        )
        || !hash_held_file(&supervisor_file, observed_digest)
        || !read_candidate(
            &supervisor_file,
            &supervisor_raw,
            &supervisor_size
        )) {
        goto cleanup;
    }
    digest_to_ascii(observed_digest, observed_sha256);
    if (strcmp(observed_sha256, supervisor_sha256) != 0
        || !json_top_string_matches(
            supervisor_raw,
            supervisor_size,
            "worker_terminal_sha256",
            worker_sha256,
            64
        )
        || !held_unchanged(&supervisor_file, NULL)
        || !held_unchanged(&claim_file, NULL)
        || !held_unchanged(&receipt_file, NULL)
        || !held_unchanged(&launch_file, NULL)
        || !held_unchanged(&candidate_file, NULL)) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (signature != NULL) {
        SecureZeroMemory(signature, signature_size);
        HeapFree(GetProcessHeap(), 0, signature);
    }
    if (key != 0) {
        NCryptFreeObject(key);
    }
    if (provider != 0) {
        NCryptFreeObject(provider);
    }
    if (supervisor_raw != NULL) {
        SecureZeroMemory(supervisor_raw, (SIZE_T)supervisor_size + 1);
        HeapFree(GetProcessHeap(), 0, supervisor_raw);
    }
    if (receipt_raw != NULL) {
        SecureZeroMemory(receipt_raw, (SIZE_T)receipt_size + 1);
        HeapFree(GetProcessHeap(), 0, receipt_raw);
    }
    if (launch_raw != NULL) {
        SecureZeroMemory(launch_raw, (SIZE_T)launch_size + 1);
        HeapFree(GetProcessHeap(), 0, launch_raw);
    }
    if (candidate_raw != NULL) {
        SecureZeroMemory(candidate_raw, (SIZE_T)candidate_size + 1);
        HeapFree(GetProcessHeap(), 0, candidate_raw);
    }
    close_held(&supervisor_file);
    close_held(&claim_file);
    close_held(&receipt_file);
    close_held(&launch_file);
    close_held(&candidate_file);
    SecureZeroMemory(&candidate, sizeof(candidate));
    SecureZeroMemory(&envelope, sizeof(envelope));
    SecureZeroMemory(candidate_digest, sizeof(candidate_digest));
    SecureZeroMemory(launch_digest, sizeof(launch_digest));
    SecureZeroMemory(unsigned_digest, sizeof(unsigned_digest));
    SecureZeroMemory(observed_digest, sizeof(observed_digest));
    SecureZeroMemory(launch_path, sizeof(launch_path));
    SecureZeroMemory(receipt_path, sizeof(receipt_path));
    SecureZeroMemory(claim_path, sizeof(claim_path));
    SecureZeroMemory(supervisor_path, sizeof(supervisor_path));
    SecureZeroMemory(candidate_sha256, sizeof(candidate_sha256));
    SecureZeroMemory(launch_sha256, sizeof(launch_sha256));
    SecureZeroMemory(observed_sha256, sizeof(observed_sha256));
    SecureZeroMemory(receipt_launch_sha256, sizeof(receipt_launch_sha256));
    SecureZeroMemory(receipt_candidate_sha256, sizeof(receipt_candidate_sha256));
    SecureZeroMemory(claim_sha256, sizeof(claim_sha256));
    SecureZeroMemory(supervisor_sha256, sizeof(supervisor_sha256));
    SecureZeroMemory(worker_sha256, sizeof(worker_sha256));
    return ok;
}

#endif
#ifdef F3_BROKER_TESTING
static int create_persistent_test_cng_key(void) {
    NCRYPT_PROV_HANDLE provider = 0;
    NCRYPT_KEY_HANDLE key = 0;
    DWORD bits = F3_EXECUTION_RSA_BITS;
    DWORD export_policy = 0;
    int ok = 0;
    if (wcsncmp(
            F3_BROKER_CNG_KEY_NAME,
            F3_DISPOSABLE_KEY_PREFIX,
            wcslen(F3_DISPOSABLE_KEY_PREFIX)
        ) != 0
        || !cng_key_absent(F3_BROKER_CNG_KEY_NAME)
        || f3_broker_open_cng_provider(&provider) != ERROR_SUCCESS
        || NCryptCreatePersistedKey(
            provider,
            &key,
            F3_BROKER_CNG_ALGORITHM,
            F3_BROKER_CNG_KEY_NAME,
            0,
            0
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
        || NCryptFinalizeKey(key, 0) != ERROR_SUCCESS) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (key != 0) {
        NCryptFreeObject(key);
    }
    if (provider != 0) {
        NCryptFreeObject(provider);
    }
    return ok;
}

static int export_persistent_test_cng_public(void) {
    NCRYPT_PROV_HANDLE provider = 0;
    NCRYPT_KEY_HANDLE key = 0;
    unsigned char *blob = NULL;
    DWORD blob_size = 0;
    unsigned char digest[32];
    char sha256[65];
    size_t index;
    int ok = 0;
    memset(digest, 0, sizeof(digest));
    memset(sha256, 0, sizeof(sha256));
    if (wcsncmp(
            F3_BROKER_CNG_KEY_NAME,
            F3_DISPOSABLE_KEY_PREFIX,
            wcslen(F3_DISPOSABLE_KEY_PREFIX)
        ) != 0
        || f3_broker_open_cng_provider(&provider) != ERROR_SUCCESS
        || NCryptOpenKey(
            provider,
            &key,
            F3_BROKER_CNG_KEY_NAME,
            0,
            0
        ) != ERROR_SUCCESS
        || NCryptExportKey(
            key,
            0,
            BCRYPT_RSAPUBLIC_BLOB,
            NULL,
            NULL,
            0,
            &blob_size,
            0
        ) != ERROR_SUCCESS
        || blob_size == 0) {
        goto cleanup;
    }
    blob = (unsigned char *)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        blob_size
    );
    if (blob == NULL
        || NCryptExportKey(
            key,
            0,
            BCRYPT_RSAPUBLIC_BLOB,
            NULL,
            blob,
            blob_size,
            &blob_size,
            0
        ) != ERROR_SUCCESS
        || !hash_memory(blob, blob_size, digest)) {
        goto cleanup;
    }
    digest_to_ascii(digest, sha256);
    if (fputs("public_blob_hex=", stdout) < 0) {
        goto cleanup;
    }
    for (index = 0; index < blob_size; ++index) {
        if (fprintf(stdout, "%02x", blob[index]) < 0) {
            goto cleanup;
        }
    }
    if (fprintf(stdout, "\npublic_blob_sha256=%s\n", sha256) < 0
        || fflush(stdout) != 0) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (blob != NULL) {
        SecureZeroMemory(blob, blob_size);
        HeapFree(GetProcessHeap(), 0, blob);
    }
    if (key != 0) {
        NCryptFreeObject(key);
    }
    if (provider != 0) {
        NCryptFreeObject(provider);
    }
    SecureZeroMemory(digest, sizeof(digest));
    SecureZeroMemory(sha256, sizeof(sha256));
    return ok;
}

static int persistent_test_cng_key_present(void) {
    NCRYPT_PROV_HANDLE provider = 0;
    NCRYPT_KEY_HANDLE key = 0;
    int ok = open_persistent_cng_signing_key(&provider, &key);
    if (key != 0) {
        NCryptFreeObject(key);
    }
    if (provider != 0) {
        NCryptFreeObject(provider);
    }
    return ok;
}

static int delete_persistent_test_cng_key(void) {
    NCRYPT_PROV_HANDLE provider = 0;
    NCRYPT_KEY_HANDLE key = 0;
    int deleted = 0;
    if (wcsncmp(
            F3_BROKER_CNG_KEY_NAME,
            F3_DISPOSABLE_KEY_PREFIX,
            wcslen(F3_DISPOSABLE_KEY_PREFIX)
        ) != 0
        || f3_broker_open_cng_provider(&provider) != ERROR_SUCCESS
        || NCryptOpenKey(
            provider,
            &key,
            F3_BROKER_CNG_KEY_NAME,
            0,
            0
        ) != ERROR_SUCCESS) {
        goto cleanup;
    }
    deleted = NCryptDeleteKey(key, 0) == ERROR_SUCCESS;
    key = 0;

cleanup:
    if (key != 0) {
        NCryptFreeObject(key);
    }
    if (provider != 0) {
        NCryptFreeObject(provider);
    }
    return deleted && cng_key_absent(F3_BROKER_CNG_KEY_NAME);
}

static int test_protected_completion_namespace(
    const wchar_t *candidate_path
) {
    static const unsigned char test_receipt[] = "{}\n";
    HeldFile candidate_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile launch_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    ProtectedCompletionNamespace completion;
    ProductionCandidate candidate;
    SignedEnvelope envelope;
    unsigned char *candidate_raw = NULL;
    unsigned char *launch_raw = NULL;
    DWORD candidate_size = 0;
    DWORD launch_size = 0;
    unsigned char launch_digest[32];
    wchar_t launch_path[32768];
    wchar_t completed_path[32768];
    wchar_t parent_path[32768];
    wchar_t denied_path[32768];
    wchar_t replacement_path[32768];
    char launch_sha256[65];
    HANDLE restricted_token = NULL;
    HANDLE denied_file = INVALID_HANDLE_VALUE;
    HANDLE test_receipt_file = INVALID_HANDLE_VALUE;
    FILE_DISPOSITION_INFO disposition = {TRUE};
    DWORD denied_error = ERROR_SUCCESS;
    DWORD replacement_error = ERROR_SUCCESS;
    int impersonating = 0;
    int replacement_blocked = 0;
    int ok = 0;
    memset(&completion, 0, sizeof(completion));
    initialize_held_directory_chain(&completion.chain);
    memset(&candidate, 0, sizeof(candidate));
    memset(&envelope, 0, sizeof(envelope));
    f3_test_completion_namespace_stage = 1;
    if (!open_held_file(
            candidate_path,
            F3_MAX_CANDIDATE_BYTES,
            &candidate_file
        )
        || !read_candidate(
            &candidate_file,
            &candidate_raw,
            &candidate_size
        )
        || !parse_production_candidate(
            candidate_raw,
            candidate_size,
            &candidate
        )
        || !byte_slice_to_wide(
            candidate.launch_authorization_path,
            launch_path,
            sizeof(launch_path) / sizeof(launch_path[0])
        )
        || !open_held_file(
            launch_path,
            F3_MAX_AUTHORIZATION_BYTES,
            &launch_file
        )
        || !hash_held_file(&launch_file, launch_digest)
        || !read_candidate(&launch_file, &launch_raw, &launch_size)
        || !parse_signed_envelope(launch_raw, launch_size, &envelope)
        || !validate_current_launch_payload(&envelope, &candidate)) {
        goto cleanup;
    }
    f3_test_completion_namespace_stage = 2;
    digest_to_ascii(launch_digest, launch_sha256);
    if (!hold_protected_completion_namespace(
            candidate.execution_ledger_root,
            launch_sha256,
            completed_path,
            &completion
        )
        || !parent_directory(
            completed_path,
            parent_path,
            sizeof(parent_path) / sizeof(parent_path[0])
        )
        || _snwprintf_s(
            denied_path,
            sizeof(denied_path) / sizeof(denied_path[0]),
            _TRUNCATE,
            L"%ls\\.restricted-write-test-%lu",
            parent_path,
            GetCurrentProcessId()
        ) <= 0
        || _snwprintf_s(
            replacement_path,
            sizeof(replacement_path) / sizeof(replacement_path[0]),
            _TRUNCATE,
            L"%ls.replacement-test-%lu",
            parent_path,
            GetCurrentProcessId()
        ) <= 0) {
        goto cleanup;
    }
    f3_test_completion_namespace_stage = 3;
    if (!create_production_restricted_token(&restricted_token)) {
        goto cleanup;
    }
    f3_test_completion_namespace_stage = 4;
    if (!restricted_token_cannot_mutate_completion_namespace(
            restricted_token,
            &completion.chain
        )) {
        goto cleanup;
    }
    f3_test_completion_namespace_stage = 5;
    if (!ImpersonateLoggedOnUser(restricted_token)) {
        goto cleanup;
    }
    impersonating = 1;
    denied_file = CreateFileW(
        denied_path,
        GENERIC_WRITE | DELETE,
        0,
        NULL,
        CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL,
        NULL
    );
    denied_error = GetLastError();
    if (!RevertToSelf()) {
        goto cleanup;
    }
    impersonating = 0;
    f3_test_completion_namespace_stage = 6;
    if (denied_file != INVALID_HANDLE_VALUE
        || denied_error != ERROR_ACCESS_DENIED) {
        goto cleanup;
    }
    SetLastError(ERROR_SUCCESS);
    if (!MoveFileExW(parent_path, replacement_path, 0)) {
        replacement_error = GetLastError();
        replacement_blocked =
            replacement_error == ERROR_SHARING_VIOLATION
            || replacement_error == ERROR_ACCESS_DENIED;
    } else {
        MoveFileExW(replacement_path, parent_path, 0);
    }
    f3_test_completion_namespace_stage = 7;
    if (!replacement_blocked) {
        goto cleanup;
    }
    f3_test_completion_namespace_stage = 8;
    if (!atomic_write_persistent_completion(
            &completion,
            completed_path,
            test_receipt,
            (DWORD)(sizeof(test_receipt) - 1)
        )) {
        goto cleanup;
    }
    test_receipt_file = CreateFileW(
        completed_path,
        DELETE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        NULL,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
        NULL
    );
    if (test_receipt_file == INVALID_HANDLE_VALUE
        || !SetFileInformationByHandle(
            test_receipt_file,
            FileDispositionInfo,
            &disposition,
            sizeof(disposition)
        )) {
        goto cleanup;
    }
    CloseHandle(test_receipt_file);
    test_receipt_file = INVALID_HANDLE_VALUE;
    if (GetFileAttributesW(completed_path) != INVALID_FILE_ATTRIBUTES
        || GetLastError() != ERROR_FILE_NOT_FOUND) {
        goto cleanup;
    }
    f3_test_completion_namespace_stage = 9;
    ok = held_directory_chain_unchanged(&completion.chain)
        && held_unchanged(&launch_file, NULL)
        && held_unchanged(&candidate_file, NULL);

cleanup:
    if (test_receipt_file != INVALID_HANDLE_VALUE) {
        CloseHandle(test_receipt_file);
    }
    if (denied_file != INVALID_HANDLE_VALUE) {
        CloseHandle(denied_file);
        if (!impersonating) {
            DeleteFileW(denied_path);
        }
    }
    if (impersonating && !RevertToSelf()) {
        ok = 0;
    }
    if (restricted_token != NULL) {
        CloseHandle(restricted_token);
    }
    if (launch_raw != NULL) {
        SecureZeroMemory(launch_raw, (SIZE_T)launch_size + 1);
        HeapFree(GetProcessHeap(), 0, launch_raw);
    }
    if (candidate_raw != NULL) {
        SecureZeroMemory(candidate_raw, (SIZE_T)candidate_size + 1);
        HeapFree(GetProcessHeap(), 0, candidate_raw);
    }
    close_held_directory_chain(&completion.chain);
    close_held(&launch_file);
    close_held(&candidate_file);
    SecureZeroMemory(&completion, sizeof(completion));
    SecureZeroMemory(&candidate, sizeof(candidate));
    SecureZeroMemory(&envelope, sizeof(envelope));
    SecureZeroMemory(launch_digest, sizeof(launch_digest));
    SecureZeroMemory(launch_path, sizeof(launch_path));
    SecureZeroMemory(completed_path, sizeof(completed_path));
    SecureZeroMemory(parent_path, sizeof(parent_path));
    SecureZeroMemory(denied_path, sizeof(denied_path));
    SecureZeroMemory(replacement_path, sizeof(replacement_path));
    SecureZeroMemory(launch_sha256, sizeof(launch_sha256));
    return ok;
}

static int test_isolated_supervisor_runtime_startup(void) {
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    HANDLE restricted_token = NULL;
    wchar_t command_line[32768];
    wchar_t runtime_directory[32768];
    wchar_t *environment = NULL;
    DWORD exit_code = 1;
    int written;
    int ok = 0;
    memset(&startup, 0, sizeof(startup));
    memset(&process, 0, sizeof(process));
    startup.cb = sizeof(startup);
    written = _snwprintf_s(
        command_line,
        sizeof(command_line) / sizeof(command_line[0]),
        _TRUNCATE,
        L"\"%ls\" -I -B -S -P -c \"import sys\"",
        F3_BROKER_RUNTIME_PATH
    );
    if (written <= 0
        || !parent_directory(
            F3_BROKER_RUNTIME_PATH,
            runtime_directory,
            sizeof(runtime_directory) / sizeof(runtime_directory[0])
        )
        || (environment = sanitized_environment(
            L"factor-v3-formal-native-broker-runtime-test/v1"
        )) == NULL
        || !create_test_supervisor_compatibility_token(&restricted_token)
        || !CreateProcessAsUserW(
            restricted_token,
            F3_BROKER_RUNTIME_PATH,
            command_line,
            NULL,
            NULL,
            FALSE,
            CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
            environment,
            runtime_directory,
            &startup,
            &process
        )) {
        goto cleanup;
    }
    if (WaitForSingleObject(process.hProcess, 30000) != WAIT_OBJECT_0
        || !GetExitCodeProcess(process.hProcess, &exit_code)) {
        goto cleanup;
    }
    if (exit_code != 0) {
        SetLastError(exit_code);
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
    if (restricted_token != NULL) {
        CloseHandle(restricted_token);
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

int f3_broker_launch_production_supervisor(const wchar_t *candidate_path) {
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
    HeldFile runtime = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile source = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile candidate_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile launch_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile credential = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile claim_file = {INVALID_HANDLE_VALUE, 0, 0, {0}};
    HeldFile supervisor_completed_file = {
        INVALID_HANDLE_VALUE, 0, 0, {0}
    };
    HeldFile worker_terminal_file = {
        INVALID_HANDLE_VALUE, 0, 0, {0}
    };
    ProtectedCompletionNamespace completion_namespace;
    ProductionCandidate candidate;
    SignedEnvelope envelope;
    unsigned char *candidate_raw = NULL;
    unsigned char *launch_raw = NULL;
    DWORD candidate_size = 0;
    DWORD launch_size = 0;
    unsigned char candidate_digest[32];
    unsigned char launch_digest[32];
    wchar_t launch_path[32768];
    wchar_t command_line[32768];
    wchar_t runtime_directory[32768];
    wchar_t *environment = NULL;
    unsigned char completed_frame[1024];
    DWORD completed_frame_size = 0;
    char native_terminal[1024];
    int native_terminal_size = 0;
    wchar_t native_completed_path[32768];
    char authorization_sha256[65];
    char candidate_sha256[65];
    char native_completed_receipt_sha256[65];
    char claim_sha256[65];
    char supervisor_completed_sha256[65];
    char worker_terminal_sha256[65];
    char response[128];
    DWORD response_size = 0;
    DWORD written = 0;
    DWORD child_exit = 1;
    DWORD worker_terminal_bytes = 0;
    BOOL process_in_job = FALSE;
    int attributes_initialized = 0;
    int child_created = 0;
    int restricted_token_ready = 0;
    int native_completion_published = 0;
    int ok = 0;
    const char *action = NULL;
    memset(&startup, 0, sizeof(startup));
    memset(&process, 0, sizeof(process));
    memset(&limits, 0, sizeof(limits));
    memset(&pipe_security, 0, sizeof(pipe_security));
    memset(&candidate, 0, sizeof(candidate));
    memset(&envelope, 0, sizeof(envelope));
    memset(&completion_namespace, 0, sizeof(completion_namespace));
    initialize_held_directory_chain(&completion_namespace.chain);
    memset(launch_path, 0, sizeof(launch_path));
    memset(native_completed_path, 0, sizeof(native_completed_path));
    memset(authorization_sha256, 0, sizeof(authorization_sha256));
    memset(candidate_sha256, 0, sizeof(candidate_sha256));
    memset(
        native_completed_receipt_sha256,
        0,
        sizeof(native_completed_receipt_sha256)
    );
    memset(claim_sha256, 0, sizeof(claim_sha256));
    memset(supervisor_completed_sha256, 0, sizeof(supervisor_completed_sha256));
    memset(worker_terminal_sha256, 0, sizeof(worker_terminal_sha256));
    pipe_security.nLength = sizeof(pipe_security);
    pipe_security.bInheritHandle = TRUE;
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    F3_PRODUCTION_LAUNCH_STAGE(1);
    if (!f3_broker_validate_production_candidate(candidate_path)) {
#ifdef F3_BROKER_TESTING
        f3_test_production_launch_stage =
            100 + f3_test_production_validation_stage;
#endif
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(11);
    if (!open_held_file(
            F3_BROKER_RUNTIME_PATH,
            F3_MAX_MANIFEST_FILE_BYTES,
            &runtime
        )
        || !verify_held_hash(&runtime, F3_BROKER_RUNTIME_SHA256)
        || !open_held_file(
            F3_BROKER_SOURCE_PATH,
            F3_MAX_MANIFEST_FILE_BYTES,
            &source
        )
        || !verify_held_hash(&source, F3_BROKER_SOURCE_SHA256)
        || !open_held_file(
            candidate_path,
            F3_MAX_CANDIDATE_BYTES,
            &candidate_file
        )
        || !hash_held_file(&candidate_file, candidate_digest)
        || !filename_matches_candidate_digest(
            candidate_path,
            candidate_digest
        )
        || !read_candidate(
            &candidate_file,
            &candidate_raw,
            &candidate_size
        )
        || !parse_production_candidate(
            candidate_raw,
            candidate_size,
            &candidate
        )
        || !byte_slice_to_wide(
            candidate.launch_authorization_path,
            launch_path,
            sizeof(launch_path) / sizeof(launch_path[0])
        )
        || !open_held_file(
            launch_path,
            F3_MAX_AUTHORIZATION_BYTES,
            &launch_file
        )
        || !hash_held_file(&launch_file, launch_digest)
        || !filename_matches_digest_suffix(
            launch_path,
            launch_digest,
            L".json"
        )
        || !read_candidate(&launch_file, &launch_raw, &launch_size)
        || !parse_signed_envelope(launch_raw, launch_size, &envelope)
        || !validate_current_launch_payload(&envelope, &candidate)
        || (candidate.action == ACTION_RESUME
            && !validate_resume_lineage(&candidate, &envelope))) {
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(2);
    action = candidate.action == ACTION_RUN ? "run" : "resume";
    digest_to_ascii(launch_digest, authorization_sha256);
    digest_to_ascii(candidate_digest, candidate_sha256);
    if (!hold_protected_completion_namespace(
            candidate.execution_ledger_root,
            authorization_sha256,
            native_completed_path,
            &completion_namespace
        )
        || !production_supervisor_command_line(
            launch_path,
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
    environment = sanitized_environment(
        L"factor-v3-formal-native-broker-supervisor/v1"
    );
    if (environment == NULL) {
        goto cleanup;
    }
#ifdef F3_BROKER_TESTING
    if (F3_BROKER_TESTING_SUPERVISOR_TOKEN_COMPATIBILITY == 1) {
        restricted_token_ready = create_test_supervisor_compatibility_token(
            &restricted_token
        );
    } else {
#endif
        restricted_token_ready =
            create_production_restricted_token(&restricted_token)
            && restricted_token_cannot_read_path(
                restricted_token,
                F3_BROKER_CREDENTIAL_SLOT_PATH
            );
#ifdef F3_BROKER_TESTING
    }
#endif
    if (!restricted_token_ready
        || !restricted_token_cannot_mutate_completion_namespace(
            restricted_token,
            &completion_namespace.chain
        )
        || !CreatePipe(
            &child_input,
            &parent_input,
            &pipe_security,
            0
        )
        || !CreatePipe(
            &parent_output,
            &child_output,
            &pipe_security,
            0
        )
        || !SetHandleInformation(
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
    F3_PRODUCTION_LAUNCH_STAGE(3);
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
    F3_PRODUCTION_LAUNCH_STAGE(4);
    startup.StartupInfo.hStdInput = child_input;
    startup.StartupInfo.hStdOutput = child_output;
#ifdef F3_BROKER_TESTING
    startup.StartupInfo.hStdError = child_output;
#else
    startup.StartupInfo.hStdError = null_error;
#endif
    inherited_handles[0] = child_input;
    inherited_handles[1] = child_output;
    inherited_handles[2] = null_error;
    job = CreateJobObjectW(NULL, NULL);
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (job == NULL
        || !SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &limits,
            sizeof(limits)
        )) {
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(5);
    InitializeProcThreadAttributeList(NULL, 2, 0, &attributes_size);
    attributes = (PPROC_THREAD_ATTRIBUTE_LIST)HeapAlloc(
        GetProcessHeap(),
        HEAP_ZERO_MEMORY,
        attributes_size
    );
    if (attributes_size == 0
        || attributes == NULL
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
    F3_PRODUCTION_LAUNCH_STAGE(6);
    startup.lpAttributeList = attributes;
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
    F3_PRODUCTION_LAUNCH_STAGE(7);
    CloseHandle(child_input);
    child_input = INVALID_HANDLE_VALUE;
    CloseHandle(child_output);
    child_output = INVALID_HANDLE_VALUE;
    CloseHandle(null_error);
    null_error = INVALID_HANDLE_VALUE;
    if (ResumeThread(process.hThread) == (DWORD)-1) {
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(71);
    if (!read_production_ready(
            parent_output,
            process.hProcess,
            authorization_sha256,
            action
        )) {
        DWORD diagnostic_exit = STILL_ACTIVE;
        WaitForSingleObject(process.hProcess, 1000);
        if (GetExitCodeProcess(process.hProcess, &diagnostic_exit)
            && diagnostic_exit != STILL_ACTIVE) {
            SetLastError(diagnostic_exit);
        }
#ifdef F3_BROKER_TESTING
        {
            DWORD diagnostic_available = 0;
            DWORD diagnostic_read = 0;
            unsigned char diagnostic[4096];
            if (PeekNamedPipe(
                    parent_output,
                    NULL,
                    0,
                    NULL,
                    &diagnostic_available,
                    NULL
                )
                && diagnostic_available > 0
                && diagnostic_available <= sizeof(diagnostic)
                && ReadFile(
                    parent_output,
                    diagnostic,
                    diagnostic_available,
                    &diagnostic_read,
                    NULL
                )
                && diagnostic_read > 0) {
                fwrite(diagnostic, 1, diagnostic_read, stderr);
            }
            SecureZeroMemory(diagnostic, sizeof(diagnostic));
        }
#endif
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(72);
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
    F3_PRODUCTION_LAUNCH_STAGE(8);
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
    F3_PRODUCTION_LAUNCH_STAGE(9);
    CloseHandle(parent_input);
    parent_input = INVALID_HANDLE_VALUE;
    if (!read_production_completed(
            parent_output,
            process.hProcess,
            authorization_sha256,
            completed_frame,
            &completed_frame_size,
            claim_sha256,
            supervisor_completed_sha256,
            worker_terminal_sha256
        )) {
#ifdef F3_BROKER_TESTING
        {
            DWORD diagnostic_available = 0;
            DWORD diagnostic_read = 0;
            unsigned char diagnostic[4096];
            WaitForSingleObject(process.hProcess, 1000);
            if (PeekNamedPipe(
                    parent_output,
                    NULL,
                    0,
                    NULL,
                    &diagnostic_available,
                    NULL
                )
                && diagnostic_available > 0
                && diagnostic_available <= sizeof(diagnostic)
                && ReadFile(
                    parent_output,
                    diagnostic,
                    diagnostic_available,
                    &diagnostic_read,
                    NULL
                )
                && diagnostic_read > 0) {
                fwrite(diagnostic, 1, diagnostic_read, stderr);
            }
            SecureZeroMemory(diagnostic, sizeof(diagnostic));
        }
#endif
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(91);
    if (WaitForSingleObject(process.hProcess, 30000) != WAIT_OBJECT_0
        || !GetExitCodeProcess(process.hProcess, &child_exit)
        || child_exit != 0
        || !held_unchanged(&credential, NULL)
        || !held_unchanged(&launch_file, NULL)
        || !held_unchanged(&candidate_file, NULL)
        || !held_unchanged(&source, F3_BROKER_SOURCE_SHA256)
        || !held_unchanged(&runtime, F3_BROKER_RUNTIME_SHA256)) {
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(92);
    if (!validate_completion_lineage_before_signing(
            &candidate,
            &envelope,
            authorization_sha256,
            claim_sha256,
            supervisor_completed_sha256,
            worker_terminal_sha256,
            &worker_terminal_bytes,
            &claim_file,
            &supervisor_completed_file,
            &worker_terminal_file
        )) {
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(93);
    if (!write_persistent_cng_completion(
            &completion_namespace,
            native_completed_path,
            authorization_sha256,
            candidate_sha256,
            claim_sha256,
            supervisor_completed_sha256,
            worker_terminal_sha256,
            worker_terminal_bytes,
            native_completed_receipt_sha256
        )) {
        goto cleanup;
    }
    native_completion_published = 1;
    F3_PRODUCTION_LAUNCH_STAGE(94);
    if (!verify_persistent_cng_completion(candidate_path)
        || !held_unchanged(&claim_file, NULL)
        || !held_unchanged(&supervisor_completed_file, NULL)
        || !held_unchanged(&worker_terminal_file, NULL)
        || !held_directory_chain_unchanged(
            &completion_namespace.chain
        )) {
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(95);
    native_terminal_size = snprintf(
        native_terminal,
        sizeof(native_terminal),
        "COMPLETED factor-v3-formal-native-broker/v2\n"
        "launch_authorization_sha256=%s\n"
        "claim_sha256=%s\n"
        "supervisor_completed_sha256=%s\n"
        "worker_terminal_sha256=%s\n"
        "native_completed_receipt_sha256=%s\n",
        authorization_sha256,
        claim_sha256,
        supervisor_completed_sha256,
        worker_terminal_sha256,
        native_completed_receipt_sha256
    );
    if (native_terminal_size <= 0
        || (size_t)native_terminal_size >= sizeof(native_terminal)
        || !WriteFile(
            GetStdHandle(STD_OUTPUT_HANDLE),
            native_terminal,
            (DWORD)native_terminal_size,
            &written,
            NULL
        )
        || written != (DWORD)native_terminal_size
        || !FlushFileBuffers(GetStdHandle(STD_OUTPUT_HANDLE))) {
        goto cleanup;
    }
    F3_PRODUCTION_LAUNCH_STAGE(10);
    ok = 1;

cleanup:
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
    if (launch_raw != NULL) {
        SecureZeroMemory(launch_raw, (SIZE_T)launch_size + 1);
        HeapFree(GetProcessHeap(), 0, launch_raw);
    }
    if (candidate_raw != NULL) {
        SecureZeroMemory(candidate_raw, (SIZE_T)candidate_size + 1);
        HeapFree(GetProcessHeap(), 0, candidate_raw);
    }
    if (!ok && native_completion_published) {
        delete_failed_persistent_completion(
            &completion_namespace,
            native_completed_receipt_sha256
        );
    }
    close_held(&credential);
    close_held(&worker_terminal_file);
    close_held(&claim_file);
    close_held(&supervisor_completed_file);
    close_held(&launch_file);
    close_held(&candidate_file);
    close_held(&source);
    close_held(&runtime);
    close_held_directory_chain(&completion_namespace.chain);
    SecureZeroMemory(&candidate, sizeof(candidate));
    SecureZeroMemory(&envelope, sizeof(envelope));
    SecureZeroMemory(
        &completion_namespace,
        sizeof(completion_namespace)
    );
    SecureZeroMemory(candidate_digest, sizeof(candidate_digest));
    SecureZeroMemory(launch_digest, sizeof(launch_digest));
    SecureZeroMemory(launch_path, sizeof(launch_path));
    SecureZeroMemory(command_line, sizeof(command_line));
    SecureZeroMemory(runtime_directory, sizeof(runtime_directory));
    SecureZeroMemory(completed_frame, sizeof(completed_frame));
    SecureZeroMemory(native_terminal, sizeof(native_terminal));
    SecureZeroMemory(native_completed_path, sizeof(native_completed_path));
    SecureZeroMemory(authorization_sha256, sizeof(authorization_sha256));
    SecureZeroMemory(candidate_sha256, sizeof(candidate_sha256));
    SecureZeroMemory(
        native_completed_receipt_sha256,
        sizeof(native_completed_receipt_sha256)
    );
    SecureZeroMemory(claim_sha256, sizeof(claim_sha256));
    SecureZeroMemory(
        supervisor_completed_sha256,
        sizeof(supervisor_completed_sha256)
    );
    SecureZeroMemory(worker_terminal_sha256, sizeof(worker_terminal_sha256));
    SecureZeroMemory(response, sizeof(response));
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
        argc == 3
        && wcscmp(
            argv[1],
            L"--test-validate-production-candidate"
        ) == 0
    ) {
        if (!f3_broker_validate_production_candidate(argv[2])) {
            fwprintf(
                stderr,
                L"native broker production candidate rejected stage=%d\n",
                f3_test_production_validation_stage
            );
            return 37;
        }
        return 0;
    }
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
    if (
        argc == 3
        && wcscmp(
            argv[1],
            L"--test-production-supervisor-launch"
        ) == 0
    ) {
        if (!f3_broker_launch_production_supervisor(argv[2])) {
            fwprintf(
                stderr,
                L"native broker production supervisor launch rejected stage=%d pipe_stage=%d pipe_error=%lu atomic_error=%lu atomic_ntstatus=0x%08lx error=%lu\n",
                f3_test_production_launch_stage,
                f3_test_pipe_stage,
                f3_test_pipe_error,
                f3_test_atomic_write_error,
                (unsigned long)f3_test_atomic_write_ntstatus,
                GetLastError()
            );
            return 38;
        }
        return 0;
    }
    if (
        argc == 3
        && wcscmp(
            argv[1],
            L"--test-fixed-worker-token-denies-secret"
        ) == 0
    ) {
        HANDLE worker_token = NULL;
        int denied = create_production_restricted_token(&worker_token)
            && restricted_token_cannot_read_path(worker_token, argv[2]);
        if (worker_token != NULL) {
            CloseHandle(worker_token);
        }
        if (!denied) {
            fwprintf(stderr, L"native broker worker token ACL rejected\n");
            return 39;
        }
        return 0;
    }
    if (
        argc == 3
        && wcscmp(
            argv[1],
            L"--test-protected-completion-namespace"
        ) == 0
    ) {
        if (!test_protected_completion_namespace(argv[2])) {
            fwprintf(
                stderr,
                L"native broker protected completion namespace rejected stage=%d launch_stage=%d atomic_error=%lu atomic_ntstatus=0x%08lx error=%lu\n",
                f3_test_completion_namespace_stage,
                f3_test_production_launch_stage,
                f3_test_atomic_write_error,
                (unsigned long)f3_test_atomic_write_ntstatus,
                GetLastError()
            );
            return 44;
        }
        return 0;
    }
    if (
        argc == 2
        && wcscmp(
            argv[1],
            L"--test-isolated-supervisor-runtime-startup"
        ) == 0
    ) {
        if (!test_isolated_supervisor_runtime_startup()) {
            fwprintf(
                stderr,
                L"native broker isolated supervisor runtime startup rejected error=%lu\n",
                GetLastError()
            );
            return 45;
        }
        return 0;
    }
    if (
        argc == 2
        && wcscmp(
            argv[1],
            L"--test-create-persistent-cng-key"
        ) == 0
    ) {
        if (!create_persistent_test_cng_key()) {
            fwprintf(stderr, L"native broker persistent CNG key creation rejected\n");
            return 40;
        }
        return 0;
    }
    if (
        argc == 2
        && wcscmp(
            argv[1],
            L"--test-export-persistent-cng-public"
        ) == 0
    ) {
        if (!export_persistent_test_cng_public()) {
            fwprintf(stderr, L"native broker persistent CNG public export rejected\n");
            return 46;
        }
        return 0;
    }
    if (
        argc == 3
        && wcscmp(
            argv[1],
            L"--test-verify-persistent-cng-completion"
        ) == 0
    ) {
        if (!verify_persistent_cng_completion(argv[2])) {
            fwprintf(stderr, L"native broker persistent CNG receipt rejected\n");
            return 41;
        }
        return 0;
    }
    if (
        argc == 2
        && wcscmp(
            argv[1],
            L"--test-persistent-cng-key-present"
        ) == 0
    ) {
        if (!persistent_test_cng_key_present()) {
            fwprintf(stderr, L"native broker persistent CNG key missing\n");
            return 42;
        }
        return 0;
    }
    if (
        argc == 2
        && wcscmp(
            argv[1],
            L"--test-delete-persistent-cng-key"
        ) == 0
    ) {
        if (!delete_persistent_test_cng_key()) {
            fwprintf(stderr, L"native broker persistent CNG key cleanup rejected\n");
            return 43;
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
    if (
        argc == 3
        && wcscmp(argv[1], L"--verify-completion") == 0
    ) {
        if (!verify_persistent_cng_completion(argv[2])) {
            fwprintf(stderr, L"native broker completion verification rejected\n");
            return 47;
        }
        return 0;
    }
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
        if (!f3_broker_validate_production_candidate(argv[2])) {
            close_held_directory_chain(&credential_chain);
            close_held_directory_chain(&source_chain);
            close_held_directory_chain(&runtime_chain);
            fwprintf(stderr, L"native broker production candidate rejected\n");
            return 37;
        }
        if (!f3_broker_launch_production_supervisor(argv[2])) {
            close_held_directory_chain(&credential_chain);
            close_held_directory_chain(&source_chain);
            close_held_directory_chain(&runtime_chain);
            fwprintf(stderr, L"native broker production supervisor launch rejected\n");
            return 38;
        }
        close_held_directory_chain(&credential_chain);
        close_held_directory_chain(&source_chain);
        close_held_directory_chain(&runtime_chain);
        return 0;
#else
        fwprintf(stderr, L"native broker production boundary is unprovisioned\n");
#endif
        return 22;
    }
    fwprintf(stderr, L"native broker invocation rejected\n");
    return 23;
}
