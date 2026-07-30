#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif

#include <windows.h>
#include <wchar.h>

static int append_character(
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

static int append_argument(
    const wchar_t *argument,
    wchar_t *output,
    size_t capacity,
    size_t *offset
) {
    const wchar_t *cursor = argument;
    if (!append_character(output, capacity, offset, L'"')) {
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
            if (!append_character(output, capacity, offset, L'\\')) {
                return 0;
            }
        }
        if (*cursor == L'\0') {
            break;
        }
        if (!append_character(output, capacity, offset, *cursor++)) {
            return 0;
        }
    }
    return append_character(output, capacity, offset, L'"');
}

static int command_line(
    wchar_t *output,
    size_t capacity,
    const wchar_t *broker,
    const wchar_t *candidate,
    const wchar_t *child_output
) {
    static const wchar_t launch[] = L"--test-launch";
    const wchar_t *arguments[] = {broker, launch, candidate, child_output};
    size_t offset = 0;
    size_t index;
    for (index = 0; index < sizeof(arguments) / sizeof(arguments[0]); ++index) {
        if (index != 0
            && !append_character(output, capacity, &offset, L' ')) {
            return 0;
        }
        if (!append_argument(
                arguments[index],
                output,
                capacity,
                &offset
            )) {
            return 0;
        }
    }
    output[offset] = L'\0';
    return 1;
}

int wmain(int argc, wchar_t **argv) {
    STARTUPINFOEXW startup;
    PROCESS_INFORMATION process;
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits;
    PPROC_THREAD_ATTRIBUTE_LIST attributes = NULL;
    SIZE_T attributes_size = 0;
    HANDLE outer_job = NULL;
    wchar_t command[32768];
    DWORD exit_code = 90;
    int attributes_initialized = 0;
    int ok = 0;
    ZeroMemory(&startup, sizeof(startup));
    ZeroMemory(&process, sizeof(process));
    ZeroMemory(&limits, sizeof(limits));
    startup.StartupInfo.cb = sizeof(startup);
    if (argc != 4
        || !command_line(
            command,
            sizeof(command) / sizeof(command[0]),
            argv[1],
            argv[2],
            argv[3]
        )) {
        goto cleanup;
    }
    outer_job = CreateJobObjectW(NULL, NULL);
    if (outer_job == NULL) {
        goto cleanup;
    }
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!SetInformationJobObject(
            outer_job,
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
            &outer_job,
            sizeof(outer_job),
            NULL,
            NULL
        )) {
        goto cleanup;
    }
    startup.lpAttributeList = attributes;
    if (!CreateProcessW(
            argv[1],
            command,
            NULL,
            NULL,
            FALSE,
            CREATE_SUSPENDED
                | CREATE_NO_WINDOW
                | EXTENDED_STARTUPINFO_PRESENT,
            NULL,
            NULL,
            &startup.StartupInfo,
            &process
        )
        || ResumeThread(process.hThread) == (DWORD)-1
        || WaitForSingleObject(process.hProcess, 60000) != WAIT_OBJECT_0
        || !GetExitCodeProcess(process.hProcess, &exit_code)
        || exit_code != 0) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (!ok && process.hProcess != NULL) {
        TerminateProcess(process.hProcess, 91);
    }
    if (process.hThread != NULL) {
        CloseHandle(process.hThread);
    }
    if (process.hProcess != NULL) {
        CloseHandle(process.hProcess);
    }
    if (attributes_initialized) {
        DeleteProcThreadAttributeList(attributes);
    }
    if (attributes != NULL) {
        HeapFree(GetProcessHeap(), 0, attributes);
    }
    if (outer_job != NULL) {
        CloseHandle(outer_job);
    }
    SecureZeroMemory(command, sizeof(command));
    return ok ? 0 : (int)exit_code;
}
