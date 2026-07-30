# Factor V3 formal native broker boundary

This directory contains a Win32 C boundary for the Factor V3 formal supervisor.
It is deliberately not a production launcher yet.

Implemented and tested:

- ordinary Python can emit only a deterministic, non-secret candidate;
- the broker maps the credential slot ID to a compile-time path;
- the signing identity is a fixed CNG provider/key/algorithm, never a private-key
  file path visible to ordinary Python;
- candidate, runtime, and reviewed-source files remain open from identity/hash
  validation through child termination;
- every opened file and every parent directory rejects reparse points, while
  files also reject multiple hard links;
- runtime and reviewed-source bytes are checked with BCrypt SHA-256;
- the child receives a newly constructed allowlisted environment;
- the child starts suspended, is assigned to a Job Object configured with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, and only then resumes;
- terminal handle metadata and public manifest hashes are rechecked.

Not implemented, and therefore fail closed:

- reviewed Windows service installation and ACL deployment;
- a native credential-handle protocol consumed by the Python supervisor;
- production `--launch`.

The checked-in manifest contains no operational paths or digests. A default
binary compiles, but always rejects production launch as unprovisioned. Test
builds inject only disposable fixtures with `F3_BROKER_TESTING`; that mode must
never be used for production.

The Windows kernel, local administrators, SCM, the configured service SID,
filesystem owner/DACL state, and the Microsoft CNG KSP are explicit external
TCB assumptions. The installer defaults to dry-run and fails closed because
this checkout is not provisioned for an administrative deployment.
