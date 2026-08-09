#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>

#include <stdint.h>
#include <stdio.h>
#include <wchar.h>

extern uint32_t f2_compound_disposable_capability_schema_version(void);
extern size_t f2_compound_disposable_capability_struct_size(void);

static uint64_t filetime_u64(FILETIME value) {
    ULARGE_INTEGER combined;
    combined.LowPart = value.dwLowDateTime;
    combined.HighPart = value.dwHighDateTime;
    return combined.QuadPart;
}

int wmain(int argc, wchar_t **argv) {
    HANDLE job;
    FILETIME creation;
    FILETIME exit_time;
    FILETIME kernel;
    FILETIME user;
    DWORD pid;
    uintptr_t job_nonce;
    BOOL process_in_job;
    int marker;

    if (argc != 3 || wcscmp(argv[1], L"--hold") != 0) {
        fwprintf(stderr, L"DISPOSABLE_COMPOUND_NATIVE_ERROR=arguments\n");
        return 64;
    }
    if (f2_compound_disposable_capability_schema_version() != 2U ||
        f2_compound_disposable_capability_struct_size() < 32U) {
        fwprintf(stderr, L"DISPOSABLE_COMPOUND_NATIVE_ERROR=abi\n");
        return 65;
    }
    job = CreateJobObjectW(NULL, NULL);
    if (job == NULL) {
        fwprintf(stderr, L"DISPOSABLE_COMPOUND_NATIVE_ERROR=job\n");
        return 66;
    }
    if (!AssignProcessToJobObject(job, GetCurrentProcess()) ||
        !IsProcessInJob(GetCurrentProcess(), job, &process_in_job) ||
        !process_in_job) {
        DWORD error_code = GetLastError();
        CloseHandle(job);
        fwprintf(
            stderr,
            L"DISPOSABLE_COMPOUND_NATIVE_ERROR=job-assignment:%lu\n",
            (unsigned long)error_code
        );
        return 67;
    }
    pid = GetCurrentProcessId();
    if (!GetProcessTimes(GetCurrentProcess(), &creation, &exit_time, &kernel, &user)) {
        CloseHandle(job);
        fwprintf(stderr, L"DISPOSABLE_COMPOUND_NATIVE_ERROR=process-time\n");
        return 68;
    }
    job_nonce = (uintptr_t)job ^ (uintptr_t)pid ^ (uintptr_t)filetime_u64(creation);
    wprintf(
        L"DISPOSABLE_COMPOUND_NATIVE_READY=%ls:%lu:%016llx:%016llx:job-assigned\n",
        argv[2],
        (unsigned long)pid,
        (unsigned long long)job_nonce,
        (unsigned long long)filetime_u64(creation)
    );
    fflush(stdout);
    marker = fgetc(stdin);
    CloseHandle(job);
    if (marker == EOF || marker == '\n') {
        wprintf(L"DISPOSABLE_COMPOUND_NATIVE_RELEASED=%ls\n", argv[2]);
        fflush(stdout);
        return 0;
    }
    return 69;
}
