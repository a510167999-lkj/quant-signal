"""Compile and replay the two-segment frozen current-pool fixture v2."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import sqlite3
import stat
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from app.durable_io import fsync_directory


FIXTURE_SCHEMA_V2 = "current-pool-offline-fixture/v2"
FIXTURE_RECEIPT_SCHEMA_V2 = "current-pool-offline-fixture-receipt/v2"
SOURCE_SEGMENT_SCHEMA = "immutable-source-lineage/v1"
RISK_SEGMENT_SCHEMA = "risk-semantic-empty-adjudication/v1"
SOURCE_CALL_EVIDENCE_SCHEMA = "current-pool-source-call-evidence/v1"
EVIDENCE_USE = "contaminated_diagnostic_historical"
AS_OF = "2026-07-03"
AS_OF_COMPACT = "20260703"
RISK_EMPTY_INDICES = tuple(range(16, 36))
SOURCE_EMPTY_INDICES = (2, 3, 6, 7)
SOURCE_ROW_CAPS = (6000,) * 8 + (2, 2, 6000, 6000, 10000, 5000)
SOURCE_TREE_SHA256 = "089d14d1bef5daf597753253f3aba75ea16a9bd3a5587fc9fc2b421b919f7d0a"
SOURCE_CONTENT_MANIFEST_SHA256 = "518e39a5e8a0f0e7bd63be5145200eced5337a1baad25a322a2c0d851784dcd5"
SOURCE_IMPORT_RECEIPT_SHA256 = "f5ef6c48022945b9e57474b2115d9438ff0371832026f198266dcb27414894f1"
SOURCE_PLAN_SHA256 = "f03dce3d2cfeddafe0d44d724b6f9aeaf65d8f55922cf791d5afa63a72dc8183"
SOURCE_CHECKPOINT_SHA256 = "072859a998dff01af8da1375685c24c60719419352fd9eeaa2c748c79aee7b45"
SOURCE_SQLITE_SHA256 = "eceef7997f8fa965ae9ef5fc2c8ccf6e77320ccfda06bf182af8011894e2e2c7"
SOURCE_DECLARED_FILES_ROOT_SHA256 = "70a919db954d06e6b292f8ddbffe0a044f10793e6fdd2bce8960b72b7f71075d"
SOURCE_CONTAMINATED_TREE_SHA256 = "4aa465b32363a3fc9c925de69dbce3abb0d0757cb8f6c571154463dae9d0b525"
SOURCE_IMPORT_RECEIPT_FILE_SHA256 = "33f1b375dce15f815e412bb773eab8e0a0c2bd8d9f669954f787016b91f1598a"
SOURCE_TREE_MANIFEST_FILE_SHA256 = "850a3651fcf382d2db3e1011bae3cc56066f5072eb8385eb02d29494de1d6dec"
SOURCE_TREE_MANIFEST_CANONICAL_SHA256 = "3c4e91cd8bc1882af52b2c4d798009effd4fea56a8a68c444c50bbc806e75749"
LEGACY_CLEAN_SNAPSHOT_ID = "115717b02872840cd47aae3ab89bfa8fb141db117b43ea286e7be557710b86e8"
LEGACY_CLEAN_MANIFEST_FILE_SHA256 = "efa8d7af9ade1bd3d1524042c6e169930447a861ef2179b4166fee9878a2edd4"
LEGACY_CLEAN_MANIFEST_CANONICAL_SHA256 = "7dfbda0662bcac74965e9a383c2e9259247c8e4ffea06f923489d2ac927a8aed"
LEGACY_CLEAN_RECEIPT_FILE_SHA256 = "37adab93d6e565d8c39f23c376d3e267c9a244933408c8e024aeb00dac04235f"
LEGACY_CLEAN_RECEIPT_CANONICAL_SHA256 = "154c3dfe46daf8a6c95701ebd5c96045c372e748e6695d22892ff8679f136fa6"
LEGACY_CONTAMINATION_AUDIT_FILE_SHA256 = "46a1b35dcfef2fd479ecc88dde3324bbf1c40518f8b25d6095d9a285360b5e38"
LEGACY_CONTAMINATION_AUDIT_CANONICAL_SHA256 = "d7239484bc3a730b136dba156766b0af565a826bd198244ffa837f616fca03cc"
CLEAN_SNAPSHOT_V2_ID = "f5b2f91c2a500935f046f40023f0b9301bce4871f5d7528cebe93172b6ebbab7"
CLEAN_V2_MANIFEST_FILE_SHA256 = "2180110c2283245f104590ab9d9e5c3e390bc318aa92ff358ba241643f6fb21b"
CLEAN_V2_MANIFEST_CANONICAL_SHA256 = "cb1c421477c8476e29942202683575d52f1d183f1eda9e13f5a216f1b76114ad"
CLEAN_V2_RECEIPT_FILE_SHA256 = "5a516b3f736d90a90b35ec49b565257173656e00f2ee312d86e0478383843cbc"
CLEAN_V2_RECEIPT_CANONICAL_SHA256 = "5958c675d33f4813b6d1bebc301e31e4d2306cfa3b888bcf74b2ad50a998bd71"
CLEAN_V2_AUDIT_FILE_SHA256 = "e47f6a3aedf2f65633d9d9c2f7d0c6a0ff4beb7d77b615585e9b2d53403403b8"
CLEAN_V2_AUDIT_CANONICAL_SHA256 = "c42b6c4d230698a60d5d2bf55b6ed68fb8543d7e40647610653ed45b7dadf17c"
CLEAN_V2_TREE_SHA256 = "875aa6e2c909a44107766a5a1dcdbb798bc6b6877cb9c929f679cf04925f4552"
INVALID_RUN_ID = "daaf52069b5544888214ba2f8fd6690e896b043301c49e227030a389da5d4e73"
INVALID_RUN_TREE_SHA256 = "9b2eb8aae31f04059a3d81edc282d299facf1faaf9727b282e8510c9b1515f86"
INVALID_AUDIT_CANONICAL_SHA256 = "160363910a9f36bb5f278f922329a12e25d8a7110460ebfb2bee9830f8873311"
INVALID_AUDIT_FILE_SHA256 = "2a0fc55630b673045d1f85cd13dcbb5c7846b0147dd8701216e12da1d08bdd72"
INVALID_RUN_MANIFEST_FILE_SHA256 = "973dff3278c6aa4696c1d5dcd42439b8132897d2b34a22e21474ec71170aa449"
INVALID_RUN_MANIFEST_CANONICAL_SHA256 = "008d094afdf3189af36607a69e5793d14edd37056d3765fb409c7047a8c74f54"
INVALID_RECEIPT_INDEX_FILE_SHA256 = "4a1fd55a26c562c76c4efb9a2e70b11d950d3eb391caa2a31e64b449d843191d"
INVALID_RECEIPT_INDEX_CANONICAL_SHA256 = "b3367768a5af30bda1bfbe4c9f8e7e90c092362b84e427166d07ae071faeceaf"
INVALID_DATABASE_FILE_SHA256 = "636ab513de6132f51c138682c48e24efa134024d22c4b2373c50fd77a4fbdf67"
INVALID_CHECKPOINT_FILE_SHA256 = "83078c236f7830344577bd6b1e123eb731ad90758b7c02ca0dc32d339e1d238b"
INVALID_CHECKPOINT_CANONICAL_SHA256 = "7980f32b7898de6eb4625b334bedfbd920789d068a9c6ae20083bf9cc25888c8"
INVALID_PLAN_FILE_SHA256 = "42150c06ba363e357d1a8e89e2531d501babdf0949491561ed7fa5501d8e34b5"
INVALID_PLAN_CANONICAL_SHA256 = "3fef48089f7a80cf4abb4b09015ddad57167e044faeed7710799ab4bbca9fa79"
_HEX = frozenset("0123456789abcdef")
_MAX_JSON_BYTES = 32 * 1024 * 1024
_MAX_RAW_BYTES = 64 * 1024 * 1024
_STK_REQUEST_FIELDS = ["ts_code", "trade_date", "up_limit", "down_limit", "pre_close"]
_STK_RESPONSE_FIELDS = ["trade_date", "ts_code", "pre_close", "up_limit", "down_limit"]
_RISK_FIELDS = {
    "stock_st": ["ts_code", "name", "type", "type_name", "trade_date"],
    "suspend_d": ["ts_code", "trade_date", "suspend_timing", "suspend_type"],
    "namechange": ["ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"],
}
_SOURCE_PLAN_ENTRY_SHA256 = (
    "a1155f6cf65c49c4544e4cbbc1625577e40f9d1f9d211d264f8be4f2ba0edb5e",
    "5913c011234fb259662acb523b82d7a1ea80ff30b1865e5edfeaa3c93f4bccff",
    "919550d81d93dbddea65fd7016405102460b98e58aa113a8ec5d316bb7b77e19",
    "2d38c9efc0137a48c7c591dacdc029b42661d49751a57fd5ace069d939cbab86",
    "d759f93e3eaa2bd95d3b9158b6fb64a9096b02d0b9c30054700a127f28cca70b",
    "b60a22efc4eab94b22e6b035fa32276358aa30e3e01599e1eb74632910f7240f",
    "b448de10036e741883a0f5e9c471d84da687634f2a8fefc4c160fba43f4fd154",
    "1e317780ad2018902c0bf621cb459d5939955ebd8faa77ded1b62e36150c43e0",
    "6f6eda597e9f312a4de05c23cec8b351f32dcb68d8f7f855fb6f24b454c12b44",
    "6f0a464086c2bf46e4524cee0b8e04abf915a464e182f5c5c71ccf2cb6d39a6a",
    "f7fce7a957da3e95fe8c76a8127dcb8617acf9b199f0f08e544e0a15a8e4680f",
    "249165b46b60a63f4c37c1f0a90e883538aef7c5a6e2859a67e274177fe14128",
    "3d7daf6b60da75719c927bacabd6d2098b2acbb34fc162a096c92f77182aafcf",
    "db315ab876c1c45bfc171058d8539a1f89c4aed3be1b0acd74db37dba9738b28",
    "f0a3225d3745a96bc0fd5e55546dbce7c872e3ebe90ab4d7bf98570e52a1bfd3",
    "80a960fc708222f6bf5b37022ea3b3c14044b5ce4bfd1c9b20f51dc2baa255bb",
    "19b098895d12378e7740260c507fb17fac30cf85d8b3188722f7beddec032293",
    "236712274ea74b291f5af56492615bca31104317fd5a206e750fdead9ddf6342",
    "230cde573202bc35fd75cda960336289e1cc89c17d39c108c6b320c53130b171",
    "1a453258f19694fd37f134733b520287cd370fc6fb956e0ae806540a7022775d",
    "4ebb1079e40af4e0b7ae4bd44550c84c17b81b06b68e65352d8bc8fb90e99f4d",
    "ed1eb90f323b475030c4ac66bc8f27825796b2d9ecfe09f11ba378b94fd4a590",
    "76b34b2b73a492aa7032b57becd73ebb4f94f0f9e2ef090564e32093593e3afc",
    "f1222e740debe8a5fc11c5cf38c2dbe98b037548d440b6aa597daf0d967338ed",
    "7cba4b0349e31ae9c4c9cdcad60bf27ce0f1004c14b404fd8c51f7598d33a6d0",
    "71a7817d3250dad5214065e95dd23647c5f322659aa1c24176fa9d31a9bcf957",
    "19a64f856222be73f97033ed52fde650a27de8e1236ad8a4e355c5028392d7ec",
    "c6588822833b9075ec29b7c71227cfa9ec094bdcaff723f5dc2b2a65c0512bcf",
    "8a2cdc5fe81c7dc2a6f1abf2f1d842522ebf309d6dbf1cb6e9ad06c9aba3b47f",
    "f5b24e490108b8963596e308623b216040445afb8becf998e7140bbdcc0e2a0e",
    "9f53e5e471cb9873e25ce0a33629ca606bb3a52c594feffece3512e48e16c586",
    "9293ddad1bb364e47980aa615a020f1eb58a89f92e8524f9670161cf46de7def",
    "51d01665db131834212812d630bfd275b13bcae479e4ab5b48d3645e7c34042e",
    "45c7af1c6ff8b48f3dfad5043e65e94fcb48b1e6e6c4a6a3bf74036e0b51916c",
    "cc54bf22ec5cfc3173debe7f2bc97811e850464952d9c25e566f0816e7fc5ef1",
    "697bd2597667f639a570e0450bd037f5f9fb2d8c3bb3cd9e36f9c772559d5863",
    "686e0e917a68bc73ce8d7841d20f6b05a4b26719d8ded808ff915624efdd0684",
    "79a396e973b87349c55ab34f034e7656d464e50abe9f72ecaf0346a2049dea3c",
    "7e8c838611f04ca822aad441139962a5acd94df506b35982508dbad9cf74282b",
    "4931359b289b3ca15fb79d083e64080bac4bc33c34fbfdbba603fd5a49a16bb1",
    "c471533391eb65c6ba02603f29233c8b3a209880bcea2acc181696760f9a40a9",
    "1ad0804ef2af7e50dcb5951296dc52f2c13b552a00791a4f8c1f296f2895325a",
    "fa7d8bf0480733692b3777fc61ff5045aa2c387687716645f5f42157515d7a4a",
    "4b8b56e0e0085559df90157a4c23b7fe8d809ce9095e6842ec6a856f1b60d878",
    "9d4102b5eca283d7c89c7916f4ae945a70e62d21b57e63bc6fa0a5a971f1485f",
    "e9f7c94fa7d36f6f8fb9ac750b229517da951dacf4162e66464f0f8b6252d5fe",
    "a114e9277892459b6598dbf87c768138e8ce6a59827e21dd44bda0ae85effeef",
    "c1912c2700588ccf319ec2d38217d2d66bffdf17e9a6644fbcc0f3b48b34988a",
    "b1e61ac6c64990d4b7f59af6926a06055ee6ec6ac6dc5b51dde391155ae5a1df",
    "3eaa427568cb0bb1a1745f56c8f089c052dcbd43dfa0cb844532ade3912ac569",
    "b0f81db1ed21745d5f73032abbb5f01b34b6b8f705ea9c3b431e6cbbdcb2c3ce",
    "03fa6c0fdee6215a001b9c862ca627a16b3dc91ca46b136776f59bc72b3b55f3",
    "6142bc39fbd1f9e45dda75a358ca9ac5f618344c5aadfea8e6e6385375521e27",
)
_EVIDENCE_CANONICAL_SHA256 = (
    "8cbcf6531c64288470bfb490a5c391137ba70156e3633253aee8fdd54c765aa8",
    "aeea4862dc82f1e5b11e52f30371487c5a1a63714141ccd3f0cc1f8f27ec7972",
    "9656ddf6518f24aa7e2e24b8ba2f2a654a7766c885f4ad47b1f2b35398a39d77",
    "780761b9ec2f17222673a073c06c5e6d764ea8065259472f0c8e81e9f504d781",
    "8589cdb6fb923554d940970fc6487664f3dc3fe369bf9feb82cda62ffe0e0b4c",
    "3b93d663547d0e9a5f51808126f7ff456f7e1ea27b9cfef754f269bf224ccc3f",
    "b48f9d5dde9c1211144e4d748f75dc8dd0c7607bd4ca3fc8c7e5c1caa44f3377",
    "a66dec5c9d0c49c9e46f0e61e08e3f6ca5b9498be7c48f11852fbf6e3d59f88d",
    "44c486380a18a67bed5565b77777b33a6d2d638e7f889702c46320a1fdeb5306",
    "4dd3cd1a7a76800d61979c1998eb57b882ab9f311aaf00f3951ac9445aae2299",
    "a80a2f6e53443801b687025bae83da18911094854527ea6dd35e2477551d13c9",
    "5b1c4cc7f8ae90c3e867ff6b84dc9e288ac8a706372b1e4f5350ebd55dbbcee8",
    "4175844e953c9544dda55d168dac2c5cdf6ad619801785a86f4eadffa199c630",
    "e5d747c313d904466dadeaa68089e7b6916e812464182abce3fc987c242b9da5",
    "52b4d83472c4df76ebf07ef5c2ac40ba0f31e018257435d0b04157a0a3865f03",
    "ac315aaf0037574dee7ca9f802eaa7f66892d014b71ece1c7a3258689d961bde",
    "1c8e77218316ae0df10cf4987c5bcd090f7ee2cd548eb74d5d1289876d3927ad",
    "7c6432356bb042fda4a415e96d41d60e4d31ccc2248e3e7cff6d6a807909a0f2",
    "4ca5a48b42405fa7f8d7ec3bb91032de141ce54c51d796782fc605bb8ee36b55",
    "8dfeb1c3bcdaf91a67352535643ea15329b506640c0a4989fa8dd6ecc7988aa4",
    "842084da8d74bf2ff485014580007f08b758f2dd50d1446fabba607b77fb6340",
    "eda8af523d8be27bed4144a4498c12ecfae969dbccae97dd4a5d0a25127fba5d",
    "f8dc9acdea3e6d1ec3d688a8af560d2f15c2a27e0246cafefb449f61c501ae37",
    "55a3c3fd5b9ccb1b2d52cc64c616a43dd03df3595ed359a34646dd33161d9629",
    "f9f3e7ffe6a651b80413c6d4061aa702a68e5fdc4981e985107fb46065e0b114",
    "1aa68269f3fefacab2fa6243728dfafa8aa7f8ce2ecc1ab71fd9fc7d11fed559",
    "2da09b7109bdda3a9771f35da362c2e432d4eb450e9921532d19bf93762a9d26",
    "b3742c3ecc2258dc8dff4f0dc51336e58018a99aed85a498e1ca2c0a87d23ebb",
    "c1502318d31b04d2ab27b43a59f01b782374aaff62dbfb11f0523993a9386805",
    "e1394b1b5fb446c746149acbaa8143906efbd8df70f9ca2097519204659f91a1",
    "d697e495558e515d6eb7acd398fd904ece9636b86c0a0a90da58f03705751330",
    "c388087c302edb8ff439affc22aad6b7ae40ab978df54df1eafce0a58b803135",
    "715224b6338a44f078336fa748d4c563c843574d70807a7fdee9ce3277484762",
    "c5278928a609090513a6f3ce91bd7224ac9343fc20a272c23eb1c52420c074ca",
    "c670ab370942332bb2111af34fe95d8a3073d2b5c558422a033eaa99f87c6eeb",
    "0db39870ed3d0bc8d9d52f17fea0af5bedd4916abaf81775d81e59c115677e70",
    "f2f2b0677fe2483127c118a75f060e2278dc67a3eefbb7322dd3bca86186a623",
    "140c2c959bda0136db69b9b233f555f035b96af42d2e020adaf71ab45db8e4ae",
    "719228cc66060248ffb4fde873308f3a14510e1ddd0202f54ee4bcb65a5451cf",
    "73498e356c4585b32657aea196321abefda3acb0e7b04cab4a795342e16eb085",
    "357a99780dd170a95b3ba200f907734cad93892c383a33d260204d7b823bedc9",
    "d27b835e743e48cc3449d17dbb938c015945475f55eb8278f74f7dd70c28286d",
    "ae99fa9738a4fea11ed02d46befd677947a2a4ebad8df216dadc289bb567d1c3",
    "c62014731518b98cd9a3f4aa7b7edcaad3720d9b5d27aeab036fc8be57b6be9b",
    "f496ae48e5e356a43bc1532f2751f5bb43a68ab9f402ddfddac4fc70622afd41",
    "fea0596b9be3b9219e6daf536207ca669db3c7b7622ab5f876239758fc561236",
    "20012ee673731badba46bbcf4d66f1eff01cbf0bbdc465cff3e1f0771081516a",
    "7c4cdc1a66a2fefa8d73cc0db8836217c3fb013441f3d9a87686c8ed0779f41e",
    "492b2170b67b19b9602ce4d028542bfb46e37b6cf4cfe82b046704a256b368e4",
    "e082a21c3b602df043ea12219e0d6b50a8ad3e53a10b45f6b03c88ed111b63d3",
    "1ba11575e69adb4cff049da41c02417c250eddd4cf99dde57ab18b7bc40cabca",
    "072d3ddf892b90724a4e614373dcef3e1d47365075fe9d0967d19be0352af1d0",
    "2064dbf6bfed2f086f77ac50fdaa1aa3240798f917a93c5314f732ae1a3f0cc4",
)


class OfflineFixtureV2Error(ValueError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha(value: Any) -> str:
    return _sha_bytes(_canonical_bytes(value))


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in _HEX for char in value)


def _add_sha(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = dict(payload)
    result[field] = _sha(payload)
    return result


def _path_is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    junction = getattr(path, "is_junction", None)
    return (
        stat.S_ISLNK(metadata.st_mode)
        or bool(attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)))
        or bool(junction and junction())
    )


def _require_directory(path: Path, label: str) -> Path:
    if not path.exists() or _path_is_link_or_reparse(path):
        raise OfflineFixtureV2Error(f"{label} is missing or unsafe")
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise OfflineFixtureV2Error(f"{label} is not a directory")
    return path.resolve(strict=True)


def _require_file(path: Path, label: str) -> Path:
    if not path.exists() or _path_is_link_or_reparse(path):
        raise OfflineFixtureV2Error(f"{label} is missing or unsafe")
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise OfflineFixtureV2Error(f"{label} is not an independent regular file")
    return path


def _read_file(path: Path, *, max_bytes: int = _MAX_JSON_BYTES) -> bytes:
    _require_file(path, "artifact")
    named = path.lstat()
    if named.st_size < 0 or named.st_size > max_bytes:
        raise OfflineFixtureV2Error("artifact size is invalid")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(str(path), flags)
    try:
        before = os.fstat(descriptor)
        if before.st_dev != named.st_dev or before.st_ino != named.st_ino or before.st_size != named.st_size:
            raise OfflineFixtureV2Error("artifact identity changed before read")
        remaining = before.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    named_after = path.lstat()
    raw = b"".join(chunks)
    if (
        len(raw) != before.st_size
        or after.st_dev != before.st_dev
        or after.st_ino != before.st_ino
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or named_after.st_dev != before.st_dev
        or named_after.st_ino != before.st_ino
        or named_after.st_size != before.st_size
        or named_after.st_mtime_ns != before.st_mtime_ns
        or _path_is_link_or_reparse(path)
    ):
        raise OfflineFixtureV2Error("artifact changed while being read")
    return raw


def _strict_json_bytes(raw: bytes, label: str) -> Any:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise OfflineFixtureV2Error(f"{label} has a duplicate key")
            result[key] = value
        return result

    def reject_constant(value):
        raise OfflineFixtureV2Error(f"{label} has a non-finite value: {value}")

    try:
        payload = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfflineFixtureV2Error(f"{label} is not strict JSON") from exc

    def reject_nonfinite(value: Any) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise OfflineFixtureV2Error(f"{label} has a non-finite value")
        if isinstance(value, Mapping):
            for nested in value.values():
                reject_nonfinite(nested)
        elif isinstance(value, list):
            for nested in value:
                reject_nonfinite(nested)

    reject_nonfinite(payload)
    return payload


def _read_json(path: Path, label: str) -> dict[str, Any]:
    payload = _strict_json_bytes(_read_file(path), label)
    if not isinstance(payload, dict):
        raise OfflineFixtureV2Error(f"{label} must be an object")
    return payload


def _verify_canonical(payload: Mapping[str, Any], field: str, label: str) -> str:
    unsigned = dict(payload)
    claimed = unsigned.pop(field, None)
    actual = _sha(unsigned)
    if not _is_sha(claimed) or not hmac.compare_digest(str(claimed), actual):
        raise OfflineFixtureV2Error(f"{label} canonical hash mismatch")
    return actual


def _relative(value: Any) -> PurePosixPath:
    if type(value) is not str or not value or value.strip() != value or "\\" in value or ":" in value:
        raise OfflineFixtureV2Error("path is not canonical POSIX")
    relative = PurePosixPath(value)
    if relative.is_absolute() or relative.as_posix() != value or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise OfflineFixtureV2Error("path is unsafe")
    return relative


def _safe_file(root: Path, value: Any) -> Path:
    relative = _relative(value)
    current = root
    for part in relative.parts:
        current /= part
        if _path_is_link_or_reparse(current):
            raise OfflineFixtureV2Error("path contains a link or reparse point")
    resolved = _require_file(current, "artifact").resolve(strict=True)
    try:
        resolved.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise OfflineFixtureV2Error("path escapes fixture root") from exc
    return current


def _file_ref(path: Path, root: Path) -> dict[str, Any]:
    raw = _read_file(path, max_bytes=_MAX_RAW_BYTES)
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": len(raw),
        "sha256": _sha_bytes(raw),
    }


def _tree_files(root: Path) -> list[dict[str, Any]]:
    root = _require_directory(root, "tree root")
    rows: list[dict[str, Any]] = []
    for base, directories, files in os.walk(root, topdown=True, followlinks=False):
        base_path = Path(base)
        _require_directory(base_path, "tree directory")
        for name in directories:
            _require_directory(base_path / name, "tree directory")
        for name in files:
            rows.append(_file_ref(base_path / name, root))
    return sorted(rows, key=lambda row: row["path"])


def _immutable_connection(path: Path) -> sqlite3.Connection:
    _require_file(path, "SQLite database")
    parent_before = _tree_files(path.parent)
    uri = f"{path.resolve(strict=True).as_uri()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        connection.close()
        raise OfflineFixtureV2Error("SQLite integrity check failed")
    parent_after = _tree_files(path.parent)
    if parent_before != parent_after:
        connection.close()
        raise OfflineFixtureV2Error("immutable SQLite open changed its directory")
    return connection


def _aware_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise OfflineFixtureV2Error(f"{label} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise OfflineFixtureV2Error(f"{label} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OfflineFixtureV2Error(f"{label} is not timezone aware")
    return parsed


def _provider_date(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise OfflineFixtureV2Error("provider HTTP Date is missing")
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise OfflineFixtureV2Error("provider HTTP Date is invalid") from exc
    if parsed.tzinfo is None:
        raise OfflineFixtureV2Error("provider HTTP Date is not aware")
    return parsed.astimezone(timezone.utc)


def _validate_clock(call: Mapping[str, Any]) -> None:
    started = _aware_timestamp(call.get("request_started_at"), "request_started_at").astimezone(timezone.utc)
    retrieved = _aware_timestamp(call.get("retrieved_at"), "retrieved_at").astimezone(timezone.utc)
    if started > retrieved or retrieved.date().isoformat() == AS_OF:
        raise OfflineFixtureV2Error("actual collection clock was confused with as_of")
    provider = _provider_date(call.get("provider_http_date"))
    skew = timedelta(seconds=300)
    if (
        provider < started.replace(microsecond=0) - skew
        or provider > retrieved.replace(microsecond=0) + skew
    ):
        raise OfflineFixtureV2Error("provider clock is outside the request interval")


def _envelope(raw: bytes, response_fields: Sequence[str]) -> tuple[dict[str, Any], list[list[Any]]]:
    if not raw:
        raise OfflineFixtureV2Error("raw response body is empty")
    payload = _strict_json_bytes(raw, "raw response")
    if (
        not isinstance(payload, dict)
        or set(payload) != {"code", "data", "msg"}
        or type(payload.get("code")) is not int
        or payload["code"] != 0
        or payload.get("msg") != "success"
        or not isinstance(payload.get("data"), dict)
        or set(payload["data"]) != {"fields", "items"}
        or payload["data"].get("fields") != list(response_fields)
        or not isinstance(payload["data"].get("items"), list)
    ):
        raise OfflineFixtureV2Error("raw response envelope is invalid")
    rows = payload["data"]["items"]
    if any(not isinstance(row, list) or len(row) != len(response_fields) for row in rows):
        raise OfflineFixtureV2Error("raw response row shape is invalid")
    identities = [_canonical_bytes(row) for row in rows]
    if len(identities) != len(set(identities)):
        raise OfflineFixtureV2Error("raw response contains duplicate rows")
    return payload, rows


def _valid_compact_date(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        return False
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return False
    return parsed.strftime("%Y%m%d") == value


def _validate_partition(call: Mapping[str, Any], rows: list[list[Any]]) -> None:
    fields = list(call["response_fields"])
    positions = {field: fields.index(field) for field in fields}
    index = call["source_index"]
    params = call["params"]
    endpoint = call["endpoint"]
    if index < 8:
        if any(
            row[positions["exchange"]] != params["exchange"]
            or row[positions["list_status"]] != params["list_status"]
            for row in rows
        ):
            raise OfflineFixtureV2Error("stock_basic partition mismatch")
    elif index in {8, 9}:
        if len(rows) != 1 or any(
            row[positions["exchange"]] != params["exchange"]
            or str(row[positions["cal_date"]]) != AS_OF_COMPACT
            or str(row[positions["is_open"]]) != "1"
            for row in rows
        ):
            raise OfflineFixtureV2Error("trade_cal partition mismatch")
    elif index in {10, 11, 12, 13, 14, 15}:
        if any(str(row[positions["trade_date"]]) != AS_OF_COMPACT for row in rows):
            raise OfflineFixtureV2Error("trade_date partition mismatch")
    elif endpoint == "namechange":
        for row in rows:
            ann_date = str(row[positions["ann_date"]])
            start_date = str(row[positions["start_date"]])
            end_date = row[positions["end_date"]]
            if not _valid_compact_date(ann_date) or not _valid_compact_date(start_date):
                raise OfflineFixtureV2Error("namechange row date is invalid")
            if end_date is not None and (not _valid_compact_date(str(end_date)) or str(end_date) < start_date):
                raise OfflineFixtureV2Error("namechange interval is invalid")
            if not params["start_date"] <= ann_date <= params["end_date"]:
                raise OfflineFixtureV2Error("namechange announcement escaped its partition")


def _rows_as_objects(fields: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    return [dict(zip(fields, row, strict=True)) for row in rows]


def _source_stage(index: int) -> str:
    if index < 8:
        return "universe"
    if index < 14:
        return "history"
    return "risk"


def _source_purpose(index: int) -> str:
    if index < 8:
        return "universe"
    if index < 10:
        return "calendar"
    if index < 13:
        return "history"
    if index == 13:
        return "market_risk"
    return "risk"


def _source_attempt_params(index: int, value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    result = dict(value)
    if index >= 8:
        for field in ("start_date", "end_date", "trade_date"):
            date_value = result.get(field)
            if isinstance(date_value, str):
                result[field] = date_value.replace("-", "")
    return result


def _verify_clean_snapshot(root: Path) -> dict[str, Any]:
    root = _require_directory(root, "clean snapshot root")
    manifest_path = root / "clean-snapshot-manifest.json"
    receipt_path = root / "clean-snapshot-receipt.json"
    audit_path = root / "contamination-audit.json"
    manifest_raw = _read_file(manifest_path)
    receipt_raw = _read_file(receipt_path)
    audit_raw = _read_file(audit_path)
    manifest = _strict_json_bytes(manifest_raw, "clean snapshot manifest")
    receipt = _strict_json_bytes(receipt_raw, "clean snapshot receipt")
    audit = _strict_json_bytes(audit_raw, "contamination audit")
    if not all(isinstance(payload, dict) for payload in (manifest, receipt, audit)):
        raise OfflineFixtureV2Error("clean snapshot metadata is not an object")
    _verify_canonical(manifest, "manifest_canonical_sha256", "clean snapshot manifest")
    _verify_canonical(receipt, "receipt_canonical_sha256", "clean snapshot receipt")
    _verify_canonical(audit, "audit_canonical_sha256", "contamination audit")
    if manifest.get("schema") == "clean-source-snapshot/v2":
        return _verify_clean_snapshot_v2(
            root=root,
            manifest_raw=manifest_raw,
            receipt_raw=receipt_raw,
            audit_raw=audit_raw,
            manifest=manifest,
            receipt=receipt,
            audit=audit,
        )
    if (
        _sha_bytes(manifest_raw) != LEGACY_CLEAN_MANIFEST_FILE_SHA256
        or manifest.get("manifest_canonical_sha256")
        != LEGACY_CLEAN_MANIFEST_CANONICAL_SHA256
        or _sha_bytes(receipt_raw) != LEGACY_CLEAN_RECEIPT_FILE_SHA256
        or receipt.get("receipt_canonical_sha256")
        != LEGACY_CLEAN_RECEIPT_CANONICAL_SHA256
        or _sha_bytes(audit_raw) != LEGACY_CONTAMINATION_AUDIT_FILE_SHA256
        or audit.get("audit_canonical_sha256")
        != LEGACY_CONTAMINATION_AUDIT_CANONICAL_SHA256
    ):
        raise OfflineFixtureV2Error("legacy clean snapshot seal mismatch")
    if (
        manifest.get("schema") != "clean-source-snapshot/v1"
        or manifest.get("status") != "clean_declared_files_only"
        or manifest.get("source_file_count") != 56
        or manifest.get("copy_parity_count") != 56
        or manifest.get("eligible_pool_count") != 0
        or manifest.get("production_recommendation_eligible") is not False
        or receipt.get("schema") != "clean-source-snapshot-receipt/v1"
        or audit.get("schema") != "current-pool-source-contamination-audit/v1"
        or audit.get("status") != "contaminated_by_sqlite_sidecars"
        or audit.get("declared_file_count") != 56
        or audit.get("actual_file_count") != 58
        or root.name != LEGACY_CLEAN_SNAPSHOT_ID
    ):
        raise OfflineFixtureV2Error("clean snapshot contract is invalid")
    lineage = manifest.get("source_lineage")
    if (
        not isinstance(lineage, dict)
        or lineage.get("old_logical_tree_sha256") != SOURCE_TREE_SHA256
        or lineage.get("frozen_content_manifest_sha256") != SOURCE_CONTENT_MANIFEST_SHA256
        or lineage.get("import_receipt_canonical_sha256") != SOURCE_IMPORT_RECEIPT_SHA256
        or receipt.get("snapshot_id") != manifest.get("snapshot_id")
        or root.name != manifest.get("snapshot_id")
    ):
        raise OfflineFixtureV2Error("clean snapshot lineage is invalid")
    source = _require_directory(root / "source", "clean source")
    actual = _tree_files(source)
    if (
        actual != manifest.get("source_files")
        or len(actual) != 56
        or _sha(actual) != SOURCE_DECLARED_FILES_ROOT_SHA256
        or manifest.get("source_files_root_sha256")
        != SOURCE_DECLARED_FILES_ROOT_SHA256
        or manifest.get("source_total_bytes")
        != sum(int(row["bytes"]) for row in actual)
    ):
        raise OfflineFixtureV2Error("clean source exact tree mismatch")
    forbidden = [row["path"] for row in actual if row["path"].endswith(("-wal", "-shm", "-journal"))]
    if forbidden:
        raise OfflineFixtureV2Error("clean source contains a SQLite sidecar")
    plan_path = source / "request_plan.json"
    checkpoint_path = source / "checkpoint.json"
    database_path = source / "pit" / "metadata.sqlite3"
    expected_audit_descriptor = {
        "path": audit_path.name,
        "bytes": len(audit_raw),
        "sha256": _sha_bytes(audit_raw),
        "canonical_sha256": audit["audit_canonical_sha256"],
    }
    expected_manifest_descriptor = {
        "path": manifest_path.name,
        "bytes": len(manifest_raw),
        "sha256": _sha_bytes(manifest_raw),
        "canonical_sha256": manifest["manifest_canonical_sha256"],
    }
    expected_extras = [
        {
            "path": "pit/metadata.sqlite3-shm",
            "bytes": 32768,
            "sha256": "fd4c9fda9cd3f9ae7c962b0ddf37232294d55580e1aa165aa06129b8549389eb",
        },
        {
            "path": "pit/metadata.sqlite3-wal",
            "bytes": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
        },
    ]
    expected_identity = {
        "schema": "clean-source-snapshot-identity/v1",
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "content_manifest_sha256": SOURCE_CONTENT_MANIFEST_SHA256,
        "import_receipt_canonical_sha256": SOURCE_IMPORT_RECEIPT_SHA256,
        "declared_files_root_sha256": SOURCE_DECLARED_FILES_ROOT_SHA256,
        "main_sqlite_sha256": SOURCE_SQLITE_SHA256,
        "contaminated_physical_tree_sha256": SOURCE_CONTAMINATED_TREE_SHA256,
        "contamination_audit_canonical_sha256": audit["audit_canonical_sha256"],
    }
    if (
        _sha_bytes(_read_file(plan_path)) != SOURCE_PLAN_SHA256
        or _sha_bytes(_read_file(checkpoint_path)) != SOURCE_CHECKPOINT_SHA256
        or _sha_bytes(_read_file(database_path, max_bytes=_MAX_RAW_BYTES)) != SOURCE_SQLITE_SHA256
        or manifest.get("contamination_audit") != expected_audit_descriptor
        or receipt.get("manifest") != expected_manifest_descriptor
        or receipt.get("contamination_audit_canonical_sha256")
        != audit["audit_canonical_sha256"]
        or receipt.get("source_files_root_sha256")
        != SOURCE_DECLARED_FILES_ROOT_SHA256
        or receipt.get("copy_parity_count") != 56
        or receipt.get("network_calls") != 0
        or receipt.get("sidecars_created_in_snapshot") != 0
        or receipt.get("source_before_after_equal") is not True
        or receipt.get("sqlite_access_contract") != "mode=ro&immutable=1"
        or audit.get("actual_files_root_sha256") != SOURCE_CONTAMINATED_TREE_SHA256
        or audit.get("declared_files_root_sha256")
        != SOURCE_DECLARED_FILES_ROOT_SHA256
        or audit.get("extra_files") != expected_extras
        or audit.get("missing_files") != []
        or audit.get("old_source_before")
        != {"file_count": 58, "files_root_sha256": SOURCE_CONTAMINATED_TREE_SHA256}
        or audit.get("old_source_after") != audit.get("old_source_before")
        or audit.get("old_source_modified") is not False
        or audit.get("old_sidecars_deleted_or_modified") is not False
        or audit.get("network_calls") != 0
        or _sha(expected_identity) != manifest.get("snapshot_id")
    ):
        raise OfflineFixtureV2Error("clean source key artifact mismatch")
    expected_root_paths = {
        "clean-snapshot-manifest.json",
        "clean-snapshot-receipt.json",
        "contamination-audit.json",
        *(f"source/{row['path']}" for row in actual),
    }
    if {row["path"] for row in _tree_files(root)} != expected_root_paths:
        raise OfflineFixtureV2Error("clean snapshot exact root tree mismatch")
    source_before = _tree_files(source)
    connection = _immutable_connection(database_path)
    try:
        table_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        )
        attempt_count = int(
            connection.execute("SELECT COUNT(*) FROM fetch_attempts").fetchone()[0]
        )
    finally:
        connection.close()
    if (
        _tree_files(source) != source_before
        or manifest.get("sqlite_immutable_probe")
        != {
            "database_sha256": SOURCE_SQLITE_SHA256,
            "fetch_attempt_count": attempt_count,
            "sidecars_created": 0,
            "table_count": table_count,
            "uri_contract": "mode=ro&immutable=1",
        }
        or table_count != 33
        or attempt_count != 14
    ):
        raise OfflineFixtureV2Error("clean snapshot immutable SQLite probe mismatch")
    return {
        "root": root,
        "source": source,
        "manifest": manifest,
        "receipt": receipt,
        "audit": audit,
        "plan": _read_json(plan_path, "source plan"),
        "checkpoint": _read_json(checkpoint_path, "source checkpoint"),
        "database": database_path,
    }


def _verify_clean_snapshot_v2(
    *,
    root: Path,
    manifest_raw: bytes,
    receipt_raw: bytes,
    audit_raw: bytes,
    manifest: Mapping[str, Any],
    receipt: Mapping[str, Any],
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        root.name != CLEAN_SNAPSHOT_V2_ID
        or manifest.get("snapshot_id") != CLEAN_SNAPSHOT_V2_ID
        or receipt.get("snapshot_id") != CLEAN_SNAPSHOT_V2_ID
        or _sha_bytes(manifest_raw) != CLEAN_V2_MANIFEST_FILE_SHA256
        or manifest.get("manifest_canonical_sha256")
        != CLEAN_V2_MANIFEST_CANONICAL_SHA256
        or _sha_bytes(receipt_raw) != CLEAN_V2_RECEIPT_FILE_SHA256
        or receipt.get("receipt_canonical_sha256")
        != CLEAN_V2_RECEIPT_CANONICAL_SHA256
        or _sha_bytes(audit_raw) != CLEAN_V2_AUDIT_FILE_SHA256
        or audit.get("audit_canonical_sha256") != CLEAN_V2_AUDIT_CANONICAL_SHA256
    ):
        raise OfflineFixtureV2Error("clean snapshot v2 seal mismatch")
    source = _require_directory(root / "source", "clean source")
    actual = _tree_files(source)
    if (
        actual != manifest.get("source_files")
        or len(actual) != 56
        or _sha(actual) != SOURCE_DECLARED_FILES_ROOT_SHA256
        or manifest.get("source_files_root_sha256")
        != SOURCE_DECLARED_FILES_ROOT_SHA256
        or manifest.get("source_file_count") != 56
        or manifest.get("copy_parity_count") != 56
    ):
        raise OfflineFixtureV2Error("clean snapshot v2 source tree mismatch")
    lineage = manifest.get("source_lineage")
    tree_path = root / "lineage" / "source-tree-manifest.json"
    import_path = root / "lineage" / "source-import-receipt.json"
    tree_raw = _read_file(tree_path)
    import_raw = _read_file(import_path)
    expected_tree_ref = {
        "path": "lineage/source-tree-manifest.json",
        "bytes": len(tree_raw),
        "sha256": SOURCE_TREE_MANIFEST_FILE_SHA256,
        "canonical_sha256": SOURCE_TREE_MANIFEST_CANONICAL_SHA256,
    }
    expected_import_ref = {
        "path": "lineage/source-import-receipt.json",
        "bytes": len(import_raw),
        "sha256": SOURCE_IMPORT_RECEIPT_FILE_SHA256,
        "canonical_sha256": SOURCE_IMPORT_RECEIPT_SHA256,
    }
    if (
        not isinstance(lineage, Mapping)
        or lineage
        != {
            "old_logical_tree_sha256": SOURCE_TREE_SHA256,
            "frozen_content_manifest_sha256": SOURCE_CONTENT_MANIFEST_SHA256,
            "tree_manifest": expected_tree_ref,
            "import_receipt": expected_import_ref,
        }
        or _sha_bytes(tree_raw) != SOURCE_TREE_MANIFEST_FILE_SHA256
        or _sha_bytes(import_raw) != SOURCE_IMPORT_RECEIPT_FILE_SHA256
    ):
        raise OfflineFixtureV2Error("clean snapshot v2 self-contained lineage mismatch")
    observed_at = audit.get("observed_at_utc")
    _aware_timestamp(observed_at, "observed_at_utc")
    expected_extras = [
        {
            "path": "pit/metadata.sqlite3-shm",
            "bytes": 32768,
            "sha256": "fd4c9fda9cd3f9ae7c962b0ddf37232294d55580e1aa165aa06129b8549389eb",
        },
        {
            "path": "pit/metadata.sqlite3-wal",
            "bytes": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
        },
    ]
    expected_audit = {
        "schema": "current-pool-source-contamination-audit/v2",
        "status": "contaminated_by_sqlite_sidecars",
        "cause": "evidence-supported likely SQLite sidecar creation; exact creating process is not asserted",
        "observed_at_utc": observed_at,
        "frozen_source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_content_manifest_sha256": SOURCE_CONTENT_MANIFEST_SHA256,
        "declared_file_count": 56,
        "actual_file_count": 58,
        "declared_files_root_sha256": SOURCE_DECLARED_FILES_ROOT_SHA256,
        "actual_files_root_sha256": SOURCE_CONTAMINATED_TREE_SHA256,
        "extra_files": expected_extras,
        "missing_files": [],
        "main_sqlite_sha256": SOURCE_SQLITE_SHA256,
        "source_before": {
            "file_count": 58,
            "files_root_sha256": SOURCE_CONTAMINATED_TREE_SHA256,
        },
        "source_after": {
            "file_count": 58,
            "files_root_sha256": SOURCE_CONTAMINATED_TREE_SHA256,
        },
        "source_modified": False,
        "sidecars_deleted_or_modified": False,
        "network_calls": 0,
    }
    unsigned_audit = dict(audit)
    unsigned_audit.pop("audit_canonical_sha256", None)
    if unsigned_audit != expected_audit:
        raise OfflineFixtureV2Error("clean snapshot v2 contamination audit mismatch")
    expected_audit_ref = {
        "path": "contamination-audit.json",
        "bytes": len(audit_raw),
        "sha256": CLEAN_V2_AUDIT_FILE_SHA256,
        "canonical_sha256": CLEAN_V2_AUDIT_CANONICAL_SHA256,
    }
    manifest_core = dict(manifest)
    for field in (
        "manifest_canonical_sha256",
        "manifest_content_canonical_sha256",
        "receipt_identity_canonical_sha256",
        "snapshot_id",
    ):
        manifest_core.pop(field, None)
    if (
        manifest.get("status") != "clean_declared_files_only"
        or manifest.get("created_at_utc") != observed_at
        or manifest.get("contamination_audit") != expected_audit_ref
        or manifest.get("manifest_content_canonical_sha256") != _sha(manifest_core)
        or manifest.get("source_total_bytes")
        != sum(int(row["bytes"]) for row in actual)
        or manifest.get("self_contained_lineage_file_count") != 2
        or manifest.get("external_paths") != 0
        or manifest.get("symlink_reparse_hardlink_count") != 0
        or manifest.get("network_calls") != 0
        or manifest.get("eligible_pool_count") != 0
        or manifest.get("production_recommendation_eligible") is not False
    ):
        raise OfflineFixtureV2Error("clean snapshot v2 manifest mismatch")
    receipt_identity_core = dict(receipt)
    for field in (
        "receipt_canonical_sha256",
        "receipt_identity_canonical_sha256",
        "snapshot_id",
        "manifest",
    ):
        receipt_identity_core.pop(field, None)
    receipt_identity_sha = _sha(receipt_identity_core)
    expected_manifest_ref = {
        "path": "clean-snapshot-manifest.json",
        "bytes": len(manifest_raw),
        "sha256": CLEAN_V2_MANIFEST_FILE_SHA256,
        "canonical_sha256": CLEAN_V2_MANIFEST_CANONICAL_SHA256,
    }
    identity = {
        "schema": "clean-source-snapshot-identity/v2",
        "manifest_content_canonical_sha256": manifest[
            "manifest_content_canonical_sha256"
        ],
        "receipt_identity_canonical_sha256": receipt_identity_sha,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "declared_files_root_sha256": SOURCE_DECLARED_FILES_ROOT_SHA256,
        "contaminated_physical_tree_sha256": SOURCE_CONTAMINATED_TREE_SHA256,
    }
    if (
        receipt.get("receipt_identity_canonical_sha256") != receipt_identity_sha
        or manifest.get("receipt_identity_canonical_sha256") != receipt_identity_sha
        or receipt.get("manifest") != expected_manifest_ref
        or _sha(identity) != CLEAN_SNAPSHOT_V2_ID
    ):
        raise OfflineFixtureV2Error("clean snapshot v2 identity mismatch")
    database_path = source / "pit" / "metadata.sqlite3"
    source_before = _tree_files(source)
    connection = _immutable_connection(database_path)
    try:
        table_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        )
        attempt_count = int(
            connection.execute("SELECT COUNT(*) FROM fetch_attempts").fetchone()[0]
        )
    finally:
        connection.close()
    if (
        _tree_files(source) != source_before
        or table_count != 33
        or attempt_count != 14
        or manifest.get("sqlite_immutable_probe")
        != {
            "uri_contract": "mode=ro&immutable=1",
            "table_count": 33,
            "fetch_attempt_count": 14,
            "sidecars_created": 0,
            "database_sha256": SOURCE_SQLITE_SHA256,
        }
    ):
        raise OfflineFixtureV2Error("clean snapshot v2 immutable SQLite mismatch")
    expected_paths = {
        *(f"source/{row['path']}" for row in actual),
        "lineage/source-tree-manifest.json",
        "lineage/source-import-receipt.json",
        "contamination-audit.json",
        "clean-snapshot-manifest.json",
        "clean-snapshot-receipt.json",
    }
    actual_tree = _tree_files(root)
    if (
        {row["path"] for row in actual_tree} != expected_paths
        or _sha(actual_tree) != CLEAN_V2_TREE_SHA256
    ):
        raise OfflineFixtureV2Error("clean snapshot v2 exact root tree mismatch")
    return {
        "root": root,
        "source": source,
        "manifest": dict(manifest),
        "receipt": dict(receipt),
        "audit": dict(audit),
        "plan": _read_json(source / "request_plan.json", "source plan"),
        "checkpoint": _read_json(source / "checkpoint.json", "source checkpoint"),
        "database": database_path,
    }


def _source_generation_evidence(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in connection.execute("SELECT * FROM stock_basic_generation_shards"):
        result[row["attempt_id"]] = dict(row)
    for row in connection.execute("SELECT * FROM market_session_generation_shards"):
        result[row["attempt_id"]] = dict(row)
    for row in connection.execute("SELECT * FROM receipts"):
        result[f"receipt:{row['dataset']}:{row['partition_key']}"] = dict(row)
    return result


def _source_calls(clean: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[tuple[str, bytes]]]:
    plan = clean["plan"]
    checkpoint = clean["checkpoint"]
    calls = plan.get("planned_calls")
    if (
        plan.get("as_of") != AS_OF
        or plan.get("planned_call_count") != 53
        or not isinstance(calls, list)
        or len(calls) != 53
        or checkpoint.get("completed_call_indices") != list(range(53))
    ):
        raise OfflineFixtureV2Error("source plan/checkpoint is invalid")
    connection = _immutable_connection(clean["database"])
    try:
        attempts = [dict(row) for row in connection.execute("SELECT * FROM fetch_attempts ORDER BY attempt_sequence")]
        generation = _source_generation_evidence(connection)
    finally:
        connection.close()
    if len(attempts) != 14:
        raise OfflineFixtureV2Error("source immutable segment requires 14 attempts")
    output: list[dict[str, Any]] = []
    evidence_files: list[tuple[str, bytes]] = []
    for index, (plan_call, attempt, row_cap) in enumerate(zip(calls[:14], attempts, SOURCE_ROW_CAPS, strict=True)):
        if attempt["attempt_sequence"] != index + 1 or attempt["endpoint"] != plan_call.get("endpoint"):
            raise OfflineFixtureV2Error("source attempt order mismatch")
        if attempt["row_cap"] != row_cap or attempt["http_status"] != 200 or attempt["body_complete"] != 1 or attempt["outcome"] != "captured":
            raise OfflineFixtureV2Error("source attempt receipt metadata mismatch")
        raw_path = _safe_file(clean["source"] / "pit", attempt["raw_path"])
        source_raw_relative = _relative(attempt["raw_path"])
        if source_raw_relative.parts[0] != "raw":
            raise OfflineFixtureV2Error("source attempt raw path is outside PIT raw")
        raw = _read_file(raw_path, max_bytes=_MAX_RAW_BYTES)
        if len(raw) != attempt["raw_bytes"] or _sha_bytes(raw) != attempt["raw_sha256"]:
            raise OfflineFixtureV2Error("source attempt raw mismatch")
        response_fields = json.loads(attempt["fields_json"])
        request_fields = plan_call.get("fields")
        envelope, rows = _envelope(raw, response_fields)
        if index == 12:
            if request_fields != _STK_REQUEST_FIELDS or response_fields != _STK_RESPONSE_FIELDS or len(rows) != 7677:
                raise OfflineFixtureV2Error("stk_limit request/response field contract mismatch")
        elif request_fields != response_fields:
            raise OfflineFixtureV2Error("source request/response fields unexpectedly differ")
        if len(rows) >= row_cap:
            raise OfflineFixtureV2Error("source response reached its row cap")
        _validate_partition(
            {
                "source_index": index,
                "endpoint": plan_call["endpoint"],
                "params": plan_call["params"],
                "response_fields": response_fields,
            },
            rows,
        )
        headers = json.loads(attempt["response_headers_json"])
        clock = json.loads(attempt["clock_attestation_json"])
        if not clock.get("synchronized") or not headers.get("date"):
            raise OfflineFixtureV2Error("source clock evidence is invalid")
        plan_entry_sha = _sha(plan_call)
        if plan_entry_sha != _SOURCE_PLAN_ENTRY_SHA256[index]:
            raise OfflineFixtureV2Error("source plan entry hash mismatch")
        source_lineage: dict[str, Any]
        if index < 8:
            source_lineage = generation.get(attempt["attempt_id"], {})
        elif index in {8, 9}:
            source_lineage = generation.get(f"receipt:trade_cal:{attempt['partition_key']}", {})
        else:
            source_lineage = generation.get(attempt["attempt_id"], {})
        if not source_lineage:
            raise OfflineFixtureV2Error("source generation/receipt lineage is missing")
        evidence_core = {
            "schema": SOURCE_CALL_EVIDENCE_SCHEMA,
            "source_index": index,
            "attempt": attempt,
            "generation_or_receipt": source_lineage,
            "source_plan_entry_canonical_sha256": plan_entry_sha,
            "raw_items_sha256": _sha(rows),
        }
        evidence = _add_sha(evidence_core, "evidence_canonical_sha256")
        if evidence["evidence_canonical_sha256"] != _EVIDENCE_CANONICAL_SHA256[index]:
            raise OfflineFixtureV2Error("source evidence seal mismatch")
        evidence_raw = _canonical_bytes(evidence) + b"\n"
        evidence_path = f"evidence/source/{index:02d}-{evidence['evidence_canonical_sha256']}.json"
        evidence_files.append((evidence_path, evidence_raw))
        call = {
            "sequence": index + 1,
            "source_index": index,
            "segment": SOURCE_SEGMENT_SCHEMA,
            "stage": _source_stage(index),
            "endpoint": plan_call["endpoint"],
            "params": plan_call["params"],
            "request_fields": request_fields,
            "request_fields_sha256": _sha(request_fields),
            "response_fields": response_fields,
            "response_fields_sha256": _sha(response_fields),
            "source_plan_entry_sha256": plan_entry_sha,
            "raw_path": PurePosixPath(
                "raw", "source-pit", *source_raw_relative.parts[1:]
            ).as_posix(),
            "source_physical_raw_path": PurePosixPath(
                "pit", *source_raw_relative.parts
            ).as_posix(),
            "raw_bytes": len(raw),
            "raw_sha256": attempt["raw_sha256"],
            "http_status": 200,
            "provider_code": envelope["code"],
            "provider_message": envelope["msg"],
            "request_started_at": attempt["started_at"],
            "retrieved_at": attempt["retrieved_at"],
            "provider_http_date": headers["date"],
            "row_cap": row_cap,
            "row_count": len(rows),
            "raw_items_sha256": _sha(rows),
            "normalized_rows_sha256": source_lineage.get("normalized_sha256"),
            "items_empty": not rows,
            "semantic_empty_adjudicated": False,
            "evidence_kind": SOURCE_SEGMENT_SCHEMA,
            "evidence_path": evidence_path,
            "evidence_bytes": len(evidence_raw),
            "evidence_file_sha256": _sha_bytes(evidence_raw),
            "evidence_canonical_sha256": evidence["evidence_canonical_sha256"],
        }
        _validate_clock(call)
        output.append({"call": call, "raw": raw})
    return output, evidence_files


def _run_tree_sha(root: Path) -> str:
    return _sha(_tree_files(root))


def _risk_calls(
    clean: Mapping[str, Any], run_root: Path, audit_path: Path
) -> tuple[list[dict[str, Any]], list[tuple[str, bytes]], dict[str, Any]]:
    run_root = _require_directory(run_root, "invalid risk run")
    if run_root.name != INVALID_RUN_ID or _run_tree_sha(run_root) != INVALID_RUN_TREE_SHA256:
        raise OfflineFixtureV2Error("invalid risk run tree mismatch")
    audit_raw = _read_file(_require_file(audit_path, "invalid audit"))
    if _sha_bytes(audit_raw) != INVALID_AUDIT_FILE_SHA256:
        raise OfflineFixtureV2Error("invalid audit file hash mismatch")
    audit = _strict_json_bytes(audit_raw, "invalid audit")
    if not isinstance(audit, dict):
        raise OfflineFixtureV2Error("invalid audit is not an object")
    _verify_canonical(audit, "audit_canonical_sha256", "invalid audit")
    if (
        audit.get("audit_canonical_sha256") != INVALID_AUDIT_CANONICAL_SHA256
        or audit.get("audit_status") != "invalid_fail_closed"
        or audit.get("audited_run", {}).get("run_id") != INVALID_RUN_ID
        or audit.get("audited_run", {}).get("run_tree_sha256") != INVALID_RUN_TREE_SHA256
        or audit.get("finding", {}).get("zero_row_source_indices") != {"first": 16, "last": 35}
    ):
        raise OfflineFixtureV2Error("invalid audit binding mismatch")
    manifest_path = run_root / "manifest.json"
    manifest = _read_json(manifest_path, "invalid run manifest")
    _verify_canonical(manifest, "manifest_canonical_sha256", "invalid run manifest")
    if manifest.get("schema") != "current-pool-risk-evidence-run/v1" or manifest.get("status") != "complete":
        raise OfflineFixtureV2Error("invalid v1 run manifest shape mismatch")
    plan_path = run_root / "plan.json"
    plan = _read_json(plan_path, "invalid run plan")
    _verify_canonical(plan, "plan_canonical_sha256", "invalid run plan")
    index_path = run_root / "receipt-index.json"
    index_payload = _read_json(index_path, "risk receipt index")
    _verify_canonical(index_payload, "receipt_index_canonical_sha256", "risk receipt index")
    if (
        _sha_bytes(_read_file(manifest_path)) != INVALID_RUN_MANIFEST_FILE_SHA256
        or manifest.get("manifest_canonical_sha256")
        != INVALID_RUN_MANIFEST_CANONICAL_SHA256
        or _sha_bytes(_read_file(plan_path)) != INVALID_PLAN_FILE_SHA256
        or plan.get("plan_canonical_sha256") != INVALID_PLAN_CANONICAL_SHA256
        or _sha_bytes(_read_file(index_path)) != INVALID_RECEIPT_INDEX_FILE_SHA256
        or index_payload.get("receipt_index_canonical_sha256")
        != INVALID_RECEIPT_INDEX_CANONICAL_SHA256
        or _sha_bytes(_read_file(run_root / "checkpoint.json"))
        != INVALID_CHECKPOINT_FILE_SHA256
        or _sha_bytes(_read_file(run_root / "metadata.sqlite3", max_bytes=_MAX_RAW_BYTES))
        != INVALID_DATABASE_FILE_SHA256
    ):
        raise OfflineFixtureV2Error("invalid risk run evidence seal mismatch")
    if (
        index_payload.get("actual_calls") != 39
        or index_payload.get("formal_receipts") != 39
        or index_payload.get("endpoint_counts") != {"namechange": 37, "stock_st": 1, "suspend_d": 1}
        or len(index_payload.get("receipts", [])) != 39
    ):
        raise OfflineFixtureV2Error("risk receipt index cardinality mismatch")
    database = run_root / "metadata.sqlite3"
    connection = _immutable_connection(database)
    try:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT p.*, a.request_started_at, a.retrieved_at, a.elapsed_ns,
                       a.http_status, a.provider_code, a.provider_message_json,
                       a.response_headers_json, a.clock_attestation_json,
                       a.body_complete, a.raw_path, a.raw_bytes, a.raw_sha256,
                       a.row_count, a.rows_sha256, a.outcome,
                       r.receipt_path, r.receipt_canonical_sha256
                FROM planned_calls p
                JOIN fetch_attempts a USING(run_sequence)
                JOIN receipts r USING(run_sequence)
                ORDER BY p.run_sequence
                """
            )
        ]
    finally:
        connection.close()
    if len(rows) != 39 or [row["source_index"] for row in rows] != list(range(14, 53)):
        raise OfflineFixtureV2Error("risk SQLite call order mismatch")
    source_plan = clean["plan"]["planned_calls"]
    plan_calls = plan.get("calls")
    index_rows = index_payload["receipts"]
    output: list[dict[str, Any]] = []
    evidence_files: list[tuple[str, bytes]] = []
    allowlist: list[dict[str, Any]] = []
    for offset, (dbrow, plan_call, index_row) in enumerate(zip(rows, plan_calls, index_rows, strict=True)):
        source_index = 14 + offset
        source_call = source_plan[source_index]
        if _sha(source_call) != _SOURCE_PLAN_ENTRY_SHA256[source_index]:
            raise OfflineFixtureV2Error("risk source plan entry hash mismatch")
        if (
            plan_call["source_index"] != source_index
            or dbrow["run_sequence"] != offset + 1
            or dbrow["endpoint"] != source_call["endpoint"]
            or json.loads(dbrow["params_json"]) != source_call["params"]
            or json.loads(dbrow["fields_json"]) != source_call["fields"]
            or dbrow["row_cap"] != 10000
            or dbrow["http_status"] != 200
            or dbrow["provider_code"] != 0
            or dbrow["body_complete"] != 1
            or dbrow["outcome"] != "stored"
            or index_row["source_index"] != source_index
            or index_row["receipt_canonical_sha256"] != dbrow["receipt_canonical_sha256"]
        ):
            raise OfflineFixtureV2Error("risk DB/plan/index lineage mismatch")
        receipt_path = _safe_file(run_root, dbrow["receipt_path"])
        receipt_raw = _read_file(receipt_path)
        receipt = _strict_json_bytes(receipt_raw, "formal risk receipt")
        if not isinstance(receipt, dict):
            raise OfflineFixtureV2Error("formal risk receipt is not an object")
        _verify_canonical(receipt, "receipt_canonical_sha256", "formal risk receipt")
        if receipt.get("receipt_canonical_sha256") != _EVIDENCE_CANONICAL_SHA256[source_index]:
            raise OfflineFixtureV2Error("risk evidence seal mismatch")
        if (
            receipt.get("receipt_canonical_sha256") != dbrow["receipt_canonical_sha256"]
            or receipt.get("source_index") != source_index
            or receipt.get("row_cap") != 10000
            or receipt.get("request_semantics_sha256") != dbrow["request_semantics_sha256"]
        ):
            raise OfflineFixtureV2Error("formal risk receipt binding mismatch")
        raw_path = _safe_file(run_root, dbrow["raw_path"])
        raw = _read_file(raw_path, max_bytes=_MAX_RAW_BYTES)
        if len(raw) != dbrow["raw_bytes"] or _sha_bytes(raw) != dbrow["raw_sha256"]:
            raise OfflineFixtureV2Error("risk raw hash mismatch")
        mirror_candidates = list((clean["source"] / "raw" / "risk").glob(f"{source_index}_*.json"))
        if len(mirror_candidates) != 1 or _read_file(mirror_candidates[0], max_bytes=_MAX_RAW_BYTES) != raw:
            raise OfflineFixtureV2Error("risk raw differs from frozen source lineage")
        source_raw_relative = mirror_candidates[0].relative_to(clean["source"]).as_posix()
        fields = source_call["fields"]
        envelope, raw_rows = _envelope(raw, fields)
        objects = _rows_as_objects(fields, raw_rows)
        if len(raw_rows) != dbrow["row_count"] or _sha(objects) != dbrow["rows_sha256"]:
            raise OfflineFixtureV2Error("risk rows hash mismatch")
        if source_index in RISK_EMPTY_INDICES:
            if raw_rows or len(raw) != 126 or receipt.get("semantic_empty") is not True:
                raise OfflineFixtureV2Error("allowlisted risk semantic-empty evidence mismatch")
        elif not raw_rows or receipt.get("semantic_empty") is not False:
            raise OfflineFixtureV2Error("non-allowlisted risk call is empty")
        _validate_partition(
            {
                "source_index": source_index,
                "endpoint": source_call["endpoint"],
                "params": source_call["params"],
                "response_fields": fields,
            },
            raw_rows,
        )
        headers = json.loads(dbrow["response_headers_json"])
        clock = json.loads(dbrow["clock_attestation_json"])
        if (
            not clock.get("validated")
            or clock.get("provider_http_date") != headers.get("date")
            or receipt.get("clock_attestation") != clock
        ):
            raise OfflineFixtureV2Error("risk provider clock lineage mismatch")
        evidence_path = f"evidence/risk/{receipt['receipt_canonical_sha256']}.json"
        evidence_files.append((evidence_path, receipt_raw))
        call = {
            "sequence": source_index + 1,
            "source_index": source_index,
            "segment": RISK_SEGMENT_SCHEMA,
            "stage": "risk",
            "endpoint": source_call["endpoint"],
            "params": source_call["params"],
            "request_fields": fields,
            "request_fields_sha256": _sha(fields),
            "response_fields": fields,
            "response_fields_sha256": _sha(fields),
            "source_plan_entry_sha256": _sha(source_call),
            "raw_path": f"raw/source-risk/{source_index:02d}/{dbrow['raw_sha256']}.json",
            "source_physical_raw_path": source_raw_relative,
            "raw_bytes": len(raw),
            "raw_sha256": dbrow["raw_sha256"],
            "http_status": 200,
            "provider_code": envelope["code"],
            "provider_message": envelope["msg"],
            "request_started_at": dbrow["request_started_at"],
            "retrieved_at": dbrow["retrieved_at"],
            "provider_http_date": headers["date"],
            "row_cap": 10000,
            "row_count": len(raw_rows),
            "raw_items_sha256": _sha(raw_rows),
            "normalized_rows_sha256": dbrow["rows_sha256"],
            "items_empty": not raw_rows,
            "semantic_empty_adjudicated": source_index in RISK_EMPTY_INDICES,
            "evidence_kind": (
                "risk-semantic-empty-adjudicated/v1"
                if source_index in RISK_EMPTY_INDICES
                else "risk-formal-receipt/v1"
            ),
            "evidence_path": evidence_path,
            "evidence_bytes": len(receipt_raw),
            "evidence_file_sha256": _sha_bytes(receipt_raw),
            "evidence_canonical_sha256": receipt["receipt_canonical_sha256"],
        }
        _validate_clock(call)
        output.append({"call": call, "raw": raw})
        if source_index in RISK_EMPTY_INDICES:
            allowlist.append(
                {
                    "source_index": source_index,
                    "source_plan_entry_sha256": call["source_plan_entry_sha256"],
                    "raw_sha256": call["raw_sha256"],
                    "receipt_canonical_sha256": call["evidence_canonical_sha256"],
                }
            )
    adjudication_core = {
        "schema": RISK_SEGMENT_SCHEMA,
        "status": "admissible_raw_and_receipt_only",
        "historical_v1_run_status": "invalid_fail_closed",
        "historical_v1_run_success_adopted": False,
        "invalid_run_id": INVALID_RUN_ID,
        "invalid_run_tree_sha256": INVALID_RUN_TREE_SHA256,
        "invalid_audit_canonical_sha256": INVALID_AUDIT_CANONICAL_SHA256,
        "invalid_audit_file_sha256": INVALID_AUDIT_FILE_SHA256,
        "invalid_run_manifest_file_sha256": INVALID_RUN_MANIFEST_FILE_SHA256,
        "invalid_run_manifest_canonical_sha256": INVALID_RUN_MANIFEST_CANONICAL_SHA256,
        "invalid_plan_file_sha256": INVALID_PLAN_FILE_SHA256,
        "invalid_plan_canonical_sha256": INVALID_PLAN_CANONICAL_SHA256,
        "invalid_checkpoint_file_sha256": INVALID_CHECKPOINT_FILE_SHA256,
        "invalid_checkpoint_canonical_sha256": INVALID_CHECKPOINT_CANONICAL_SHA256,
        "invalid_receipt_index_file_sha256": INVALID_RECEIPT_INDEX_FILE_SHA256,
        "invalid_receipt_index_canonical_sha256": INVALID_RECEIPT_INDEX_CANONICAL_SHA256,
        "invalid_database_file_sha256": INVALID_DATABASE_FILE_SHA256,
        "allowed_source_indices": list(RISK_EMPTY_INDICES),
        "entries": allowlist,
        "eligible_pool_count": 0,
        "production_recommendation_eligible": False,
    }
    adjudication = _add_sha(adjudication_core, "adjudication_canonical_sha256")
    return output, evidence_files, adjudication


def _write_new(path: Path, raw: bytes) -> None:
    _require_directory(path.parent, "output parent")
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    if path.stat().st_nlink != 1:
        raise OfflineFixtureV2Error("published file is not an independent copy")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _write_new(path, _canonical_bytes(payload) + b"\n")


def _fixture_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": "current-pool-offline-fixture-identity/v2",
        "content_canonical_sha256": manifest["content_canonical_sha256"],
        "declared_files_root_sha256": manifest["declared_files_root_sha256"],
        "source_lineage": manifest["source_lineage"],
        "risk_adjudication": manifest["risk_adjudication"],
    }


def _fixture_content_core(manifest: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(manifest)
    result.pop("canonical_sha256", None)
    result.pop("content_canonical_sha256", None)
    result.pop("fixture_id", None)
    return result


def _publish_fixture(
    *,
    clean: Mapping[str, Any],
    source_calls: list[dict[str, Any]],
    source_evidence: list[tuple[str, bytes]],
    risk_calls: list[dict[str, Any]],
    risk_evidence: list[tuple[str, bytes]],
    adjudication: Mapping[str, Any],
    output_parent: Path,
) -> dict[str, Any]:
    calls_with_raw = source_calls + risk_calls
    calls = [entry["call"] for entry in calls_with_raw]
    if [call["source_index"] for call in calls] != list(range(53)):
        raise OfflineFixtureV2Error("composite call mapping is not exact 0..52")
    output_parent = Path(output_parent)
    if not output_parent.exists():
        output_parent.mkdir(parents=False, exist_ok=False)
        fsync_directory(output_parent.parent)
    output_parent = _require_directory(output_parent, "fixture output parent")
    lock_path = output_parent / ".current-pool-offline-fixture-v2.lock"
    lock_flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    try:
        lock_descriptor = os.open(str(lock_path), lock_flags, 0o600)
    except FileExistsError as exc:
        raise OfflineFixtureV2Error("fixture publisher lock already exists") from exc
    try:
        os.write(lock_descriptor, b"current-pool-offline-fixture/v2\n")
        os.fsync(lock_descriptor)
    finally:
        os.close(lock_descriptor)
    fsync_directory(output_parent)
    staging = output_parent / f".fixture-v2-staging-{os.getpid()}-{time.monotonic_ns()}"
    staging.mkdir(exist_ok=False)
    (staging / "raw").mkdir()
    (staging / "evidence").mkdir()
    (staging / "evidence" / "source").mkdir()
    (staging / "evidence" / "risk").mkdir()
    raw_by_path: dict[str, bytes] = {}
    for entry in calls_with_raw:
        path = entry["call"]["raw_path"]
        if path in raw_by_path and raw_by_path[path] != entry["raw"]:
            raise OfflineFixtureV2Error("content-addressed raw collision")
        raw_by_path[path] = entry["raw"]
    source_raw_paths = {
        path for path in raw_by_path if path.startswith("raw/source-pit/")
    }
    risk_raw_paths = {
        path for path in raw_by_path if path.startswith("raw/source-risk/")
    }
    if len(raw_by_path) != 50 or len(source_raw_paths) != 11 or len(risk_raw_paths) != 39:
        raise OfflineFixtureV2Error("frozen physical raw lineage is not exactly 11+39")
    for relative, raw in sorted(raw_by_path.items()):
        path = staging.joinpath(*_relative(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_new(path, raw)
    for relative, raw in sorted(source_evidence + risk_evidence):
        path = staging.joinpath(*_relative(relative).parts)
        _write_new(path, raw)
    adjudication_path = staging / "risk-semantic-empty-adjudication.json"
    _write_json(adjudication_path, adjudication)
    declared_files = _tree_files(staging)
    declared_root = _sha(declared_files)
    source_segment_calls = calls[:14]
    risk_segment_calls = calls[14:]
    manifest_core = {
        "schema": FIXTURE_SCHEMA_V2,
        "as_of": AS_OF,
        "temporal_role": "contaminated_diagnostic",
        "replay_mode": "historical",
        "evidence_use": EVIDENCE_USE,
        "eligible_pool_count": 0,
        "production_recommendation_eligible": False,
        "source_lineage": {
            "clean_snapshot_id": clean["manifest"]["snapshot_id"],
            "clean_snapshot_manifest_canonical_sha256": clean["manifest"]["manifest_canonical_sha256"],
            "clean_snapshot_manifest_file_sha256": _sha_bytes(
                _read_file(clean["root"] / "clean-snapshot-manifest.json")
            ),
            "clean_snapshot_receipt_canonical_sha256": clean["receipt"]["receipt_canonical_sha256"],
            "clean_snapshot_receipt_file_sha256": _sha_bytes(
                _read_file(clean["root"] / "clean-snapshot-receipt.json")
            ),
            "clean_snapshot_audit_canonical_sha256": clean["audit"]["audit_canonical_sha256"],
            "clean_snapshot_audit_file_sha256": _sha_bytes(
                _read_file(clean["root"] / "contamination-audit.json")
            ),
            "clean_snapshot_tree_sha256": _sha(_tree_files(clean["root"])),
            "origin_source_tree_sha256": SOURCE_TREE_SHA256,
            "origin_content_manifest_sha256": SOURCE_CONTENT_MANIFEST_SHA256,
            "origin_import_receipt_canonical_sha256": SOURCE_IMPORT_RECEIPT_SHA256,
        },
        "risk_adjudication": {
            "path": adjudication_path.name,
            "bytes": adjudication_path.stat().st_size,
            "sha256": _sha_bytes(_read_file(adjudication_path)),
            "canonical_sha256": adjudication["adjudication_canonical_sha256"],
            "invalid_run_id": INVALID_RUN_ID,
            "invalid_run_tree_sha256": INVALID_RUN_TREE_SHA256,
            "invalid_audit_canonical_sha256": INVALID_AUDIT_CANONICAL_SHA256,
            "invalid_audit_file_sha256": INVALID_AUDIT_FILE_SHA256,
            "invalid_run_manifest_file_sha256": INVALID_RUN_MANIFEST_FILE_SHA256,
            "invalid_run_manifest_canonical_sha256": INVALID_RUN_MANIFEST_CANONICAL_SHA256,
            "invalid_plan_file_sha256": INVALID_PLAN_FILE_SHA256,
            "invalid_plan_canonical_sha256": INVALID_PLAN_CANONICAL_SHA256,
            "invalid_checkpoint_file_sha256": INVALID_CHECKPOINT_FILE_SHA256,
            "invalid_checkpoint_canonical_sha256": INVALID_CHECKPOINT_CANONICAL_SHA256,
            "invalid_receipt_index_file_sha256": INVALID_RECEIPT_INDEX_FILE_SHA256,
            "invalid_receipt_index_canonical_sha256": INVALID_RECEIPT_INDEX_CANONICAL_SHA256,
            "invalid_database_file_sha256": INVALID_DATABASE_FILE_SHA256,
            "historical_v1_run_success_adopted": False,
        },
        "segments": [
            {
                "schema": SOURCE_SEGMENT_SCHEMA,
                "first_source_index": 0,
                "last_source_index": 13,
                "call_count": 14,
                "calls_root_sha256": _sha(source_segment_calls),
                "empty_source_indices": list(SOURCE_EMPTY_INDICES),
                "semantic_empty_adjudication_applied": False,
            },
            {
                "schema": RISK_SEGMENT_SCHEMA,
                "first_source_index": 14,
                "last_source_index": 52,
                "call_count": 39,
                "calls_root_sha256": _sha(risk_segment_calls),
                "semantic_empty_source_indices": list(RISK_EMPTY_INDICES),
                "historical_v1_run_success_adopted": False,
            },
        ],
        "zero_row_summary": {
            "total": 24,
            "immutable_source_lineage": list(SOURCE_EMPTY_INDICES),
            "risk_semantic_empty_adjudicated": list(RISK_EMPTY_INDICES),
        },
        "physical_raw_summary": {
            "total": 50,
            "immutable_source_lineage": 11,
            "risk_formal_lineage": 39,
        },
        "calls": calls,
        "calls_root_sha256": _sha(calls),
        "declared_files": declared_files,
        "declared_files_root_sha256": declared_root,
    }
    manifest = dict(manifest_core)
    manifest["content_canonical_sha256"] = _sha(manifest_core)
    fixture_id = _sha(_fixture_identity(manifest))
    manifest["fixture_id"] = fixture_id
    manifest = _add_sha(manifest, "canonical_sha256")
    manifest_path = staging / "manifest.json"
    _write_json(manifest_path, manifest)
    receipt_core = {
        "schema": FIXTURE_RECEIPT_SCHEMA_V2,
        "fixture_id": fixture_id,
        "manifest_file_sha256": _sha_bytes(_read_file(manifest_path)),
        "manifest_canonical_sha256": manifest["canonical_sha256"],
        "declared_files_root_sha256": declared_root,
        "call_count": 53,
        "physical_raw_file_count": 50,
        "segment_count": 2,
        "single_writer": True,
        "atomic_publish": True,
        "network_calls": 0,
        "credential_accesses": 0,
        "eligible_pool_count": 0,
        "production_recommendation_eligible": False,
    }
    receipt = _add_sha(receipt_core, "receipt_canonical_sha256")
    _write_json(staging / "fixture-receipt.json", receipt)
    target = output_parent / fixture_id
    if target.exists():
        raise OfflineFixtureV2Error("content-addressed fixture target already exists")
    for directory in sorted(
        (path for path in staging.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        fsync_directory(directory)
    fsync_directory(staging)
    expected_staging_tree = _tree_files(staging)
    os.rename(staging, target)
    fsync_directory(output_parent)
    if _tree_files(target) != expected_staging_tree:
        raise OfflineFixtureV2Error("published fixture changed during atomic rename")
    lock_path.unlink()
    fsync_directory(output_parent)
    return {
        "fixture_root": str(target),
        "fixture_id": fixture_id,
        "manifest_path": str(target / "manifest.json"),
        "manifest_file_sha256": _sha_bytes(_read_file(target / "manifest.json")),
        "manifest_canonical_sha256": manifest["canonical_sha256"],
        "receipt_file_sha256": _sha_bytes(_read_file(target / "fixture-receipt.json")),
        "receipt_canonical_sha256": receipt["receipt_canonical_sha256"],
        "declared_files_root_sha256": declared_root,
        "published_tree_sha256": _sha(_tree_files(target)),
    }


def compile_current_pool_offline_fixture_v2(
    *,
    clean_snapshot_root: str | Path,
    invalid_run_root: str | Path,
    invalid_audit_path: str | Path,
    output_parent: str | Path,
) -> dict[str, Any]:
    """Compile the exact frozen two-segment evidence into one offline fixture."""

    clean = _verify_clean_snapshot(Path(clean_snapshot_root))
    if clean["manifest"].get("schema") != "clean-source-snapshot/v2":
        raise OfflineFixtureV2Error("fixture v2 requires the sealed clean snapshot v2")
    source_calls, source_evidence = _source_calls(clean)
    risk_calls, risk_evidence, adjudication = _risk_calls(
        clean, Path(invalid_run_root), Path(invalid_audit_path)
    )
    return _publish_fixture(
        clean=clean,
        source_calls=source_calls,
        source_evidence=source_evidence,
        risk_calls=risk_calls,
        risk_evidence=risk_evidence,
        adjudication=adjudication,
        output_parent=Path(output_parent),
    )


def _verify_fixture_tree(root: Path, expected_files: set[str]) -> None:
    actual = {row["path"] for row in _tree_files(root)}
    if actual != expected_files:
        raise OfflineFixtureV2Error("fixture exact tree mismatch")


def preflight_current_pool_offline_fixture_v2(
    *,
    fixture_root: str | Path,
    expected_manifest_sha256: str,
    evidence_use: str,
) -> dict[str, Any]:
    """Strictly verify a published v2 fixture without creating any output."""

    try:
        if evidence_use != EVIDENCE_USE:
            raise OfflineFixtureV2Error("fixture use is outside contaminated diagnostic historical")
        root = _require_directory(Path(fixture_root), "fixture root")
        manifest_path = _safe_file(root, "manifest.json")
        manifest_raw = _read_file(manifest_path)
        if not _is_sha(expected_manifest_sha256) or not hmac.compare_digest(
            _sha_bytes(manifest_raw), expected_manifest_sha256
        ):
            raise OfflineFixtureV2Error("fixture manifest file hash mismatch")
        manifest = _strict_json_bytes(manifest_raw, "fixture manifest")
        if not isinstance(manifest, dict):
            raise OfflineFixtureV2Error("fixture manifest is not an object")
        _verify_canonical(manifest, "canonical_sha256", "fixture manifest")
        content_core = _fixture_content_core(manifest)
        if (
            manifest.get("content_canonical_sha256") != _sha(content_core)
            or manifest.get("fixture_id") != _sha(_fixture_identity(manifest))
        ):
            raise OfflineFixtureV2Error("fixture content-addressed identity mismatch")
        if (
            manifest.get("schema") != FIXTURE_SCHEMA_V2
            or manifest.get("as_of") != AS_OF
            or manifest.get("temporal_role") != "contaminated_diagnostic"
            or manifest.get("replay_mode") != "historical"
            or manifest.get("evidence_use") != EVIDENCE_USE
            or manifest.get("eligible_pool_count") != 0
            or manifest.get("production_recommendation_eligible") is not False
            or root.name != manifest.get("fixture_id")
        ):
            raise OfflineFixtureV2Error("fixture top-level contract mismatch")
        lineage = manifest.get("source_lineage")
        risk_binding = manifest.get("risk_adjudication")
        expected_lineage = {
            "clean_snapshot_id": CLEAN_SNAPSHOT_V2_ID,
            "clean_snapshot_manifest_canonical_sha256": CLEAN_V2_MANIFEST_CANONICAL_SHA256,
            "clean_snapshot_manifest_file_sha256": CLEAN_V2_MANIFEST_FILE_SHA256,
            "clean_snapshot_receipt_canonical_sha256": CLEAN_V2_RECEIPT_CANONICAL_SHA256,
            "clean_snapshot_receipt_file_sha256": CLEAN_V2_RECEIPT_FILE_SHA256,
            "clean_snapshot_audit_canonical_sha256": CLEAN_V2_AUDIT_CANONICAL_SHA256,
            "clean_snapshot_audit_file_sha256": CLEAN_V2_AUDIT_FILE_SHA256,
            "clean_snapshot_tree_sha256": CLEAN_V2_TREE_SHA256,
            "origin_source_tree_sha256": SOURCE_TREE_SHA256,
            "origin_content_manifest_sha256": SOURCE_CONTENT_MANIFEST_SHA256,
            "origin_import_receipt_canonical_sha256": SOURCE_IMPORT_RECEIPT_SHA256,
        }
        if not isinstance(lineage, dict) or lineage != expected_lineage:
            raise OfflineFixtureV2Error("fixture clean snapshot lineage mismatch")
        if (
            not isinstance(risk_binding, dict)
            or set(risk_binding)
            != {
                "path",
                "bytes",
                "sha256",
                "canonical_sha256",
                "invalid_run_id",
                "invalid_run_tree_sha256",
                "invalid_audit_canonical_sha256",
                "invalid_audit_file_sha256",
                "invalid_run_manifest_file_sha256",
                "invalid_run_manifest_canonical_sha256",
                "invalid_plan_file_sha256",
                "invalid_plan_canonical_sha256",
                "invalid_checkpoint_file_sha256",
                "invalid_checkpoint_canonical_sha256",
                "invalid_receipt_index_file_sha256",
                "invalid_receipt_index_canonical_sha256",
                "invalid_database_file_sha256",
                "historical_v1_run_success_adopted",
            }
            or risk_binding.get("path") != "risk-semantic-empty-adjudication.json"
            or risk_binding.get("invalid_run_id") != INVALID_RUN_ID
            or risk_binding.get("invalid_run_tree_sha256") != INVALID_RUN_TREE_SHA256
            or risk_binding.get("invalid_audit_canonical_sha256") != INVALID_AUDIT_CANONICAL_SHA256
            or risk_binding.get("invalid_audit_file_sha256") != INVALID_AUDIT_FILE_SHA256
            or risk_binding.get("invalid_run_manifest_file_sha256") != INVALID_RUN_MANIFEST_FILE_SHA256
            or risk_binding.get("invalid_run_manifest_canonical_sha256") != INVALID_RUN_MANIFEST_CANONICAL_SHA256
            or risk_binding.get("invalid_plan_file_sha256") != INVALID_PLAN_FILE_SHA256
            or risk_binding.get("invalid_plan_canonical_sha256") != INVALID_PLAN_CANONICAL_SHA256
            or risk_binding.get("invalid_checkpoint_file_sha256") != INVALID_CHECKPOINT_FILE_SHA256
            or risk_binding.get("invalid_checkpoint_canonical_sha256") != INVALID_CHECKPOINT_CANONICAL_SHA256
            or risk_binding.get("invalid_receipt_index_file_sha256") != INVALID_RECEIPT_INDEX_FILE_SHA256
            or risk_binding.get("invalid_receipt_index_canonical_sha256") != INVALID_RECEIPT_INDEX_CANONICAL_SHA256
            or risk_binding.get("invalid_database_file_sha256") != INVALID_DATABASE_FILE_SHA256
            or risk_binding.get("historical_v1_run_success_adopted") is not False
        ):
            raise OfflineFixtureV2Error("fixture external lineage anchor mismatch")
        segments = manifest.get("segments")
        expected_segments = [
            (SOURCE_SEGMENT_SCHEMA, 0, 13, 14),
            (RISK_SEGMENT_SCHEMA, 14, 52, 39),
        ]
        if not isinstance(segments, list) or len(segments) != 2:
            raise OfflineFixtureV2Error("fixture segment count mismatch")
        for segment, expected in zip(segments, expected_segments, strict=True):
            if (
                segment.get("schema"),
                segment.get("first_source_index"),
                segment.get("last_source_index"),
                segment.get("call_count"),
            ) != expected:
                raise OfflineFixtureV2Error("fixture segment boundary mismatch")
        expected_segment_keys = [
            {
                "schema",
                "first_source_index",
                "last_source_index",
                "call_count",
                "calls_root_sha256",
                "empty_source_indices",
                "semantic_empty_adjudication_applied",
            },
            {
                "schema",
                "first_source_index",
                "last_source_index",
                "call_count",
                "calls_root_sha256",
                "semantic_empty_source_indices",
                "historical_v1_run_success_adopted",
            },
        ]
        if any(
            set(segment) != keys
            for segment, keys in zip(segments, expected_segment_keys, strict=True)
        ):
            raise OfflineFixtureV2Error("fixture segment contains an unbound field")
        calls = manifest.get("calls")
        if not isinstance(calls, list) or len(calls) != 53 or [call.get("source_index") for call in calls] != list(range(53)):
            raise OfflineFixtureV2Error("fixture requires exact source indices 0..52")
        if manifest.get("calls_root_sha256") != _sha(calls):
            raise OfflineFixtureV2Error("fixture calls root mismatch")
        if segments[0].get("calls_root_sha256") != _sha(calls[:14]) or segments[1].get("calls_root_sha256") != _sha(calls[14:]):
            raise OfflineFixtureV2Error("fixture segment calls root mismatch")
        if (
            segments[0].get("empty_source_indices") != list(SOURCE_EMPTY_INDICES)
            or segments[0].get("semantic_empty_adjudication_applied") is not False
            or segments[1].get("semantic_empty_source_indices") != list(RISK_EMPTY_INDICES)
            or manifest.get("zero_row_summary")
            != {
                "total": 24,
                "immutable_source_lineage": list(SOURCE_EMPTY_INDICES),
                "risk_semantic_empty_adjudicated": list(RISK_EMPTY_INDICES),
            }
        ):
            raise OfflineFixtureV2Error("fixture zero-row segment policy mismatch")
        declared_files = manifest.get("declared_files")
        if not isinstance(declared_files, list) or manifest.get("declared_files_root_sha256") != _sha(declared_files):
            raise OfflineFixtureV2Error("fixture declared file root mismatch")
        declared = {row.get("path"): row for row in declared_files if isinstance(row, dict)}
        if len(declared) != len(declared_files):
            raise OfflineFixtureV2Error("fixture declared files are duplicated")
        declared_raw_paths = {
            path
            for path in declared
            if isinstance(path, str) and path.startswith("raw/")
        }
        if (
            manifest.get("physical_raw_summary")
            != {
                "total": 50,
                "immutable_source_lineage": 11,
                "risk_formal_lineage": 39,
            }
            or len(declared_raw_paths) != 50
            or len(
                {
                    path
                    for path in declared_raw_paths
                    if path.startswith("raw/source-pit/")
                }
            )
            != 11
            or len(
                {
                    path
                    for path in declared_raw_paths
                    if path.startswith("raw/source-risk/")
                }
            )
            != 39
        ):
            raise OfflineFixtureV2Error("fixture physical raw lineage mismatch")
        expected_tree = set(declared) | {"manifest.json", "fixture-receipt.json"}
        _verify_fixture_tree(root, expected_tree)
        receipt = _read_json(_safe_file(root, "fixture-receipt.json"), "fixture receipt")
        _verify_canonical(receipt, "receipt_canonical_sha256", "fixture receipt")
        expected_receipt = {
            "schema": FIXTURE_RECEIPT_SCHEMA_V2,
            "fixture_id": manifest["fixture_id"],
            "manifest_file_sha256": expected_manifest_sha256,
            "manifest_canonical_sha256": manifest["canonical_sha256"],
            "declared_files_root_sha256": manifest["declared_files_root_sha256"],
            "call_count": 53,
            "physical_raw_file_count": 50,
            "segment_count": 2,
            "single_writer": True,
            "atomic_publish": True,
            "network_calls": 0,
            "credential_accesses": 0,
            "eligible_pool_count": 0,
            "production_recommendation_eligible": False,
        }
        unsigned_receipt = dict(receipt)
        unsigned_receipt.pop("receipt_canonical_sha256", None)
        if unsigned_receipt != expected_receipt or any(
            type(receipt.get(field)) is not int
            for field in (
                "call_count",
                "physical_raw_file_count",
                "segment_count",
                "network_calls",
                "credential_accesses",
                "eligible_pool_count",
            )
        ):
            raise OfflineFixtureV2Error("fixture receipt mismatch")
        adjudication_path = _safe_file(root, risk_binding.get("path"))
        adjudication_raw = _read_file(adjudication_path)
        if (
            len(adjudication_raw) != risk_binding.get("bytes")
            or _sha_bytes(adjudication_raw) != risk_binding.get("sha256")
        ):
            raise OfflineFixtureV2Error("risk adjudication file descriptor mismatch")
        adjudication = _strict_json_bytes(adjudication_raw, "risk adjudication")
        if not isinstance(adjudication, dict):
            raise OfflineFixtureV2Error("risk adjudication is not an object")
        _verify_canonical(adjudication, "adjudication_canonical_sha256", "risk adjudication")
        if risk_binding.get("canonical_sha256") != adjudication.get(
            "adjudication_canonical_sha256"
        ):
            raise OfflineFixtureV2Error("risk adjudication canonical descriptor mismatch")
        expected_adjudication = {
            "schema": RISK_SEGMENT_SCHEMA,
            "status": "admissible_raw_and_receipt_only",
            "historical_v1_run_status": "invalid_fail_closed",
            "historical_v1_run_success_adopted": False,
            "invalid_run_id": INVALID_RUN_ID,
            "invalid_run_tree_sha256": INVALID_RUN_TREE_SHA256,
            "invalid_audit_canonical_sha256": INVALID_AUDIT_CANONICAL_SHA256,
            "invalid_audit_file_sha256": INVALID_AUDIT_FILE_SHA256,
            "invalid_run_manifest_file_sha256": INVALID_RUN_MANIFEST_FILE_SHA256,
            "invalid_run_manifest_canonical_sha256": INVALID_RUN_MANIFEST_CANONICAL_SHA256,
            "invalid_plan_file_sha256": INVALID_PLAN_FILE_SHA256,
            "invalid_plan_canonical_sha256": INVALID_PLAN_CANONICAL_SHA256,
            "invalid_checkpoint_file_sha256": INVALID_CHECKPOINT_FILE_SHA256,
            "invalid_checkpoint_canonical_sha256": INVALID_CHECKPOINT_CANONICAL_SHA256,
            "invalid_receipt_index_file_sha256": INVALID_RECEIPT_INDEX_FILE_SHA256,
            "invalid_receipt_index_canonical_sha256": INVALID_RECEIPT_INDEX_CANONICAL_SHA256,
            "invalid_database_file_sha256": INVALID_DATABASE_FILE_SHA256,
            "allowed_source_indices": list(RISK_EMPTY_INDICES),
            "entries": adjudication.get("entries"),
            "eligible_pool_count": 0,
            "production_recommendation_eligible": False,
        }
        unsigned_adjudication = dict(adjudication)
        unsigned_adjudication.pop("adjudication_canonical_sha256", None)
        if unsigned_adjudication != expected_adjudication:
            raise OfflineFixtureV2Error("risk adjudication policy mismatch")
        allowlist = {entry.get("source_index"): entry for entry in adjudication["entries"]}
        if list(allowlist) != list(RISK_EMPTY_INDICES):
            raise OfflineFixtureV2Error("risk semantic-empty allowlist mismatch")
        raw_catalog: dict[str, bytes] = {}
        envelope_catalog: list[dict[str, Any]] = []
        expected_call_keys = {
            "sequence",
            "source_index",
            "segment",
            "stage",
            "endpoint",
            "params",
            "request_fields",
            "request_fields_sha256",
            "response_fields",
            "response_fields_sha256",
            "source_plan_entry_sha256",
            "raw_path",
            "source_physical_raw_path",
            "raw_bytes",
            "raw_sha256",
            "http_status",
            "provider_code",
            "provider_message",
            "request_started_at",
            "retrieved_at",
            "provider_http_date",
            "row_cap",
            "row_count",
            "raw_items_sha256",
            "normalized_rows_sha256",
            "items_empty",
            "semantic_empty_adjudicated",
            "evidence_kind",
            "evidence_path",
            "evidence_bytes",
            "evidence_file_sha256",
            "evidence_canonical_sha256",
        }
        for call in calls:
            index = call["source_index"]
            if set(call) != expected_call_keys:
                raise OfflineFixtureV2Error("call contains an unbound field")
            expected_segment = SOURCE_SEGMENT_SCHEMA if index < 14 else RISK_SEGMENT_SCHEMA
            if call.get("sequence") != index + 1 or call.get("segment") != expected_segment or call.get("stage") != _source_stage(index):
                raise OfflineFixtureV2Error("call segment or sequence mismatch")
            pinned_plan_entry = {
                "endpoint": call.get("endpoint"),
                "fields": call.get("request_fields"),
                "params": call.get("params"),
                "purpose": _source_purpose(index),
            }
            if (
                call.get("source_plan_entry_sha256") != _SOURCE_PLAN_ENTRY_SHA256[index]
                or _sha(pinned_plan_entry) != _SOURCE_PLAN_ENTRY_SHA256[index]
            ):
                raise OfflineFixtureV2Error(
                    f"call source plan entry is not the frozen entry: {index}"
                )
            if call.get("request_fields_sha256") != _sha(call.get("request_fields")) or call.get("response_fields_sha256") != _sha(call.get("response_fields")):
                raise OfflineFixtureV2Error("call request/response field hash mismatch")
            if index == 12:
                if call.get("request_fields") != _STK_REQUEST_FIELDS or call.get("response_fields") != _STK_RESPONSE_FIELDS or call.get("row_cap") != 10000 or call.get("row_count") != 7677:
                    raise OfflineFixtureV2Error("stk_limit split field contract mismatch")
            elif call.get("request_fields") != call.get("response_fields"):
                raise OfflineFixtureV2Error("unexpected request/response field drift")
            if index in {8, 9} and call.get("row_cap") != 2:
                raise OfflineFixtureV2Error("trade_cal row cap mismatch")
            if index < 14 and call.get("row_cap") != SOURCE_ROW_CAPS[index]:
                raise OfflineFixtureV2Error("source row cap mismatch")
            if index >= 14 and call.get("row_cap") != 10000:
                raise OfflineFixtureV2Error("risk row cap mismatch")
            _validate_clock(call)
            raw_path = call.get("raw_path")
            if (
                not isinstance(raw_path, str)
                or PurePosixPath(raw_path).name != f"{call.get('raw_sha256')}.json"
            ):
                raise OfflineFixtureV2Error("call raw path is not content addressed")
            raw_relative = _relative(raw_path)
            if index < 14:
                if raw_relative.parts[:2] != ("raw", "source-pit"):
                    raise OfflineFixtureV2Error("source call physical raw role mismatch")
            elif raw_path != (
                f"raw/source-risk/{index:02d}/{call.get('raw_sha256')}.json"
            ):
                raise OfflineFixtureV2Error("risk call physical raw role mismatch")
            raw_file = _safe_file(root, raw_path)
            raw = raw_catalog.get(raw_path)
            if raw is None:
                raw = _read_file(raw_file, max_bytes=_MAX_RAW_BYTES)
                raw_catalog[raw_path] = raw
            if len(raw) != call.get("raw_bytes") or _sha_bytes(raw) != call.get("raw_sha256"):
                raise OfflineFixtureV2Error("call raw descriptor mismatch")
            raw_declared = declared.get(raw_path)
            if raw_declared != {"path": raw_path, "bytes": len(raw), "sha256": _sha_bytes(raw)}:
                raise OfflineFixtureV2Error("call raw is not declared exactly")
            envelope, rows = _envelope(raw, call["response_fields"])
            if len(rows) != call.get("row_count") or _sha(rows) != call.get("raw_items_sha256"):
                raise OfflineFixtureV2Error("call raw item count/hash mismatch")
            _validate_partition(call, rows)
            evidence_path = call.get("evidence_path")
            if (
                not isinstance(evidence_path, str)
                or str(call.get("evidence_canonical_sha256"))
                not in PurePosixPath(evidence_path).name
            ):
                raise OfflineFixtureV2Error("call evidence path is not content addressed")
            evidence_file = _safe_file(root, evidence_path)
            evidence_raw = _read_file(evidence_file)
            if (
                len(evidence_raw) != call.get("evidence_bytes")
                or _sha_bytes(evidence_raw) != call.get("evidence_file_sha256")
                or declared.get(evidence_path)
                != {"path": evidence_path, "bytes": len(evidence_raw), "sha256": _sha_bytes(evidence_raw)}
            ):
                raise OfflineFixtureV2Error("call evidence file descriptor mismatch")
            evidence = _strict_json_bytes(evidence_raw, "call evidence")
            if not isinstance(evidence, dict):
                raise OfflineFixtureV2Error("call evidence is not an object")
            if (
                call.get("evidence_canonical_sha256")
                != _EVIDENCE_CANONICAL_SHA256[index]
                or _sha_bytes(evidence_raw) != call.get("evidence_file_sha256")
                or call.get("http_status") != 200
                or call.get("provider_code") != 0
                or call.get("provider_message") != "success"
                or envelope.get("code") != call.get("provider_code")
                or envelope.get("msg") != call.get("provider_message")
            ):
                raise OfflineFixtureV2Error("call evidence or provider result seal mismatch")
            if index < 14:
                _verify_canonical(evidence, "evidence_canonical_sha256", "source call evidence")
                attempt = evidence.get("attempt", {})
                generation = evidence.get("generation_or_receipt", {})
                source_attempt_raw = _relative(attempt.get("raw_path"))
                expected_source_raw = PurePosixPath(
                    "pit", *source_attempt_raw.parts
                ).as_posix()
                expected_fixture_raw = PurePosixPath(
                    "raw", "source-pit", *source_attempt_raw.parts[1:]
                ).as_posix()
                if (
                    evidence.get("schema") != SOURCE_CALL_EVIDENCE_SCHEMA
                    or evidence.get("source_index") != index
                    or evidence.get("evidence_canonical_sha256") != call.get("evidence_canonical_sha256")
                    or evidence.get("source_plan_entry_canonical_sha256")
                    != call.get("source_plan_entry_sha256")
                    or evidence.get("raw_items_sha256") != call.get("raw_items_sha256")
                    or attempt.get("attempt_sequence") != index + 1
                    or attempt.get("endpoint") != call.get("endpoint")
                    or _source_attempt_params(
                        index, json.loads(attempt.get("params_json", "null"))
                    )
                    != call.get("params")
                    or json.loads(attempt.get("fields_json", "null"))
                    != call.get("response_fields")
                    or attempt.get("row_cap") != call.get("row_cap")
                    or attempt.get("http_status") != call.get("http_status")
                    or attempt.get("body_complete") != 1
                    or attempt.get("outcome") != "captured"
                    or attempt.get("raw_bytes") != call.get("raw_bytes")
                    or attempt.get("raw_sha256") != call.get("raw_sha256")
                    or attempt.get("started_at") != call.get("request_started_at")
                    or attempt.get("retrieved_at") != call.get("retrieved_at")
                    or json.loads(attempt.get("response_headers_json", "null")).get(
                        "date"
                    )
                    != call.get("provider_http_date")
                    or generation.get("normalized_sha256")
                    != call.get("normalized_rows_sha256")
                    or generation.get("row_count") != call.get("row_count")
                    or source_attempt_raw.parts[0] != "raw"
                    or call.get("source_physical_raw_path") != expected_source_raw
                    or call.get("raw_path") != expected_fixture_raw
                    or call.get("semantic_empty_adjudicated") is not False
                    or call.get("items_empty") != (index in SOURCE_EMPTY_INDICES)
                    or call.get("evidence_kind") != SOURCE_SEGMENT_SCHEMA
                ):
                    raise OfflineFixtureV2Error("source call evidence/empty policy mismatch")
            else:
                _verify_canonical(evidence, "receipt_canonical_sha256", "risk formal receipt")
                source_raw_relative = _relative(call.get("source_physical_raw_path"))
                expected_source_raw = (
                    f"raw/risk/{index}_{call.get('endpoint')}_{call.get('raw_sha256')}.json"
                )
                expected_receipt_raw = (
                    f"raw/{int(evidence.get('run_sequence', 0)):02d}_"
                    f"{PurePosixPath(expected_source_raw).name}"
                )
                if (
                    evidence.get("source_index") != index
                    or evidence.get("receipt_canonical_sha256") != call.get("evidence_canonical_sha256")
                    or evidence.get("endpoint") != call.get("endpoint")
                    or evidence.get("params") != call.get("params")
                    or evidence.get("fields") != call.get("request_fields")
                    or evidence.get("row_cap") != 10000
                    or evidence.get("raw_sha256") != call.get("raw_sha256")
                    or evidence.get("rows_sha256") != call.get("normalized_rows_sha256")
                    or evidence.get("request_started_at") != call.get("request_started_at")
                    or evidence.get("retrieved_at") != call.get("retrieved_at")
                    or evidence.get("clock_attestation", {}).get("provider_http_date") != call.get("provider_http_date")
                    or call.get("source_physical_raw_path") != expected_source_raw
                    or evidence.get("raw_path") != expected_receipt_raw
                    or source_raw_relative.parts[:2] != ("raw", "risk")
                    or not source_raw_relative.name.startswith(f"{index}_")
                    or source_raw_relative.suffix != ".json"
                ):
                    raise OfflineFixtureV2Error("risk formal receipt mismatch")
                allowed = index in RISK_EMPTY_INDICES
                if (
                    call.get("items_empty") != allowed
                    or call.get("semantic_empty_adjudicated") != allowed
                    or (allowed and (len(raw) != 126 or rows or call.get("evidence_kind") != "risk-semantic-empty-adjudicated/v1"))
                    or (not allowed and (not rows or call.get("evidence_kind") != "risk-formal-receipt/v1"))
                ):
                    raise OfflineFixtureV2Error("risk semantic-empty exact allowlist mismatch")
                if allowed:
                    allowed_entry = allowlist[index]
                    if allowed_entry != {
                        "source_index": index,
                        "source_plan_entry_sha256": call["source_plan_entry_sha256"],
                        "raw_sha256": call["raw_sha256"],
                        "receipt_canonical_sha256": call["evidence_canonical_sha256"],
                    }:
                        raise OfflineFixtureV2Error("risk semantic-empty allowlist entry mismatch")
            envelope_catalog.append(envelope)
        referenced_declared = {
            *(call["raw_path"] for call in calls),
            *(call["evidence_path"] for call in calls),
            risk_binding["path"],
        }
        if set(declared) != referenced_declared:
            raise OfflineFixtureV2Error("fixture declared files are not the exact referenced set")
        return {
            "schema": FIXTURE_SCHEMA_V2,
            "fixture_root": str(root),
            "manifest_path": str(manifest_path),
            "manifest_file_sha256": expected_manifest_sha256,
            "canonical_sha256": manifest["canonical_sha256"],
            "fixture_id": manifest["fixture_id"],
            "call_count": 53,
            "segment_counts": {SOURCE_SEGMENT_SCHEMA: 14, RISK_SEGMENT_SCHEMA: 39},
            "source_empty_count": 4,
            "risk_semantic_empty_count": 20,
            "calls": calls,
            "raw_catalog": raw_catalog,
            "envelope_catalog": envelope_catalog,
            "eligible_pool_count": 0,
            "production_recommendation_eligible": False,
        }
    except (KeyError, OSError, TypeError, ValueError, sqlite3.Error) as exc:
        raise OfflineFixtureV2Error(f"offline fixture v2 rejected: {exc}") from None


def replay_current_pool_offline_fixture_v2_calls(
    *,
    fixture_root: str | Path,
    expected_manifest_sha256: str,
    evidence_use: str,
) -> list[dict[str, Any]]:
    """Return all 53 frozen provider envelopes in source-index order, with no writes."""

    verified = preflight_current_pool_offline_fixture_v2(
        fixture_root=fixture_root,
        expected_manifest_sha256=expected_manifest_sha256,
        evidence_use=evidence_use,
    )
    return [
        {
            "source_index": call["source_index"],
            "endpoint": call["endpoint"],
            "params": dict(call["params"]),
            "request_fields": list(call["request_fields"]),
            "response_fields": list(call["response_fields"]),
            "retrieved_at": call["retrieved_at"],
            "envelope": envelope,
        }
        for call, envelope in zip(
            verified["calls"], verified["envelope_catalog"], strict=True
        )
    ]
