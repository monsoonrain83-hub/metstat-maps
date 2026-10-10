<?php
/* =====================================================================
 * MetStat — دریافت نقشه‌های مدل‌های عددی از nwp_maps.py (GitHub Actions)
 *   POST nwp_upload.php?a=begin&key=…  (JSON: model, run, name, label, params)  → شروع اجرای جدید یک مدل؛
 *        اجرای قبلیِ همان مدل همین حالا پاک می‌شود (فضا خالی می‌ماند). فهرست فایل‌های موجود برگردانده می‌شود.
 *   POST nwp_upload.php?a=put&key=…    (multipart: model, run, file, meta, png)   → ذخیرهٔ یک نقشه
 *   POST nwp_upload.php?a=sync&key=…   (JSON: model, run)                          → بازسازی فهرست کلی
 *   POST nwp_upload.php?a=end&key=…    (JSON: model, run)                          → پایان اجرا
 *   POST nwp_upload.php?a=clim&key=…                                               → فایل اقلیم (فقط برای اسکریپت، با کلید)
 *   POST nwp_upload.php?a=idxput&key=… (multipart: id, per, meta, png)             → نمودار شاخص اقلیمی (فقط تصویر نمودار؛ ENSO، NAO …)
 *   POST nwp_upload.php?a=idxend&key=…                                             → پایان به‌روزرسانی شاخص‌ها
 * کلید: همان "upload_key" در mx_config.json.
 * پوشه‌ها: data/nwp/<model>/<run>/<region>/<param>_<valid>.png ؛ فهرست کلی: data/manifest.json
 * ===================================================================== */
declare(strict_types=1);
date_default_timezone_set('UTC');
header('Cache-Control: no-store');
$DIR = __DIR__; $DATA = "$DIR/data"; $NWP = "$DATA/nwp"; @mkdir($NWP, 0755, true);
function jout($v, int $c = 200): void { http_response_code($c); header('Content-Type: application/json; charset=utf-8'); echo json_encode($v, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES); exit; }
function jload(string $f, $d) { return is_file($f) ? (json_decode((string)file_get_contents($f), true) ?? $d) : $d; }
function jsave(string $f, $v): void { $t = "$f.tmp" . getmypid(); file_put_contents($t, json_encode($v, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)); rename($t, $f); }
function rrmdir(string $d): void { if (!is_dir($d)) return; foreach (new RecursiveIteratorIterator(new RecursiveDirectoryIterator($d, FilesystemIterator::SKIP_DOTS), RecursiveIteratorIterator::CHILD_FIRST) as $f) { $f->isDir() ? @rmdir($f->getPathname()) : @unlink($f->getPathname()); } @rmdir($d); }
function logl(string $s): void { global $DATA; @file_put_contents("$DATA/fetch.log", gmdate('Y-m-d H:i:s') . " [nwp] $s\n", FILE_APPEND); }

$C = jload("$DIR/mx_config.json", null); if (!$C) jout(['error' => 'config'], 500);
$KEY = (string)($C['upload_key'] ?? '');
if ($KEY === '' || $KEY === 'CHANGE-ME' || !hash_equals($KEY, (string)($_GET['key'] ?? $_POST['key'] ?? ''))) jout(['error' => 'forbidden'], 403);
if ($_SERVER['REQUEST_METHOD'] !== 'POST') jout(['error' => 'POST only'], 405);
$a = (string)($_GET['a'] ?? '');

if ($a === 'clim') {                       // اقلیم ایستگاهی شبکه‌بندی‌شده (برای انومالی دما)؛ در پوشهٔ عمومی نیست
    $f = "$DIR/clim_tmean.npz"; if (!is_file($f)) jout(['error' => 'no clim'], 404);
    header('Content-Type: application/octet-stream'); readfile($f); exit;
}
if ($a === 'idxput' || $a === 'idxend') {      // نمودار شاخص‌های اقلیمی (تصویر)؛ بدون وابستگی به مدل/اجرا
    $ID = "$DATA/idx"; @mkdir($ID, 0755, true); $F = "$ID/index.json"; $X = jload($F, ['items' => [], 'updated' => null]);
    if ($a === 'idxend') { $X['updated'] = gmdate('c'); jsave($F, $X); jout(['ok' => true]); }
    $iid = (string)($_POST['id'] ?? ''); $per = (string)($_POST['per'] ?? '');
    if (!preg_match('~^[a-z0-9_]{2,24}$~', $iid) || !preg_match('~^[a-z0-9]{2,8}$~', $per)) jout(['error' => 'id'], 400);
    if (empty($_FILES['png']['tmp_name']) || !is_uploaded_file($_FILES['png']['tmp_name'])) jout(['error' => 'no file'], 400);
    $b = (string)file_get_contents($_FILES['png']['tmp_name']);
    if (strlen($b) < 1000 || substr($b, 0, 8) !== "\x89PNG\r\n\x1a\n") jout(['error' => 'not png'], 400);
    file_put_contents("$ID/{$iid}_{$per}.png.tmp", $b); rename("$ID/{$iid}_{$per}.png.tmp", "$ID/{$iid}_{$per}.png");
    $m = json_decode((string)($_POST['meta'] ?? '{}'), true) ?: [];
    $e = $X['items'][$iid] ?? ['per' => []];
    foreach (['fa', 'en', 'grp', 'src', 'last', 'upd', 'unit', 'kind', 'note'] as $k) if (isset($m[$k])) $e[$k] = mb_substr((string)$m[$k], 0, 300);
    $e['per'][$per] = (string)($m['perfa'] ?? $per); $X['items'][$iid] = $e; jsave($F, $X); jout(['ok' => true]);
}
$body = $a === 'put' ? $_POST : (json_decode((string)file_get_contents('php://input'), true) ?: []);
$model = (string)($body['model'] ?? ''); $run = (string)($body['run'] ?? '');
if (!preg_match('~^[a-z]{2,10}$~', $model)) jout(['error' => 'model'], 400);
if (!preg_match('~^20\d{8}$~', $run)) jout(['error' => 'run'], 400);
$MD = "$NWP/$model"; @mkdir($MD, 0755, true);
$lock = fopen("$NWP/.lock", 'c'); flock($lock, LOCK_EX);
$SF = "$MD/state.json"; $S = jload($SF, ['run' => null, 'items' => []]);

function manifest(): void {
    global $NWP, $DATA;
    $P = jload("$NWP/params.json", []);
    $man = ['source' => 'nwp', 'updated' => gmdate('c'), 'status' => 'complete', 'run' => null, 'models' => [], 'regions' => ['iran' => ['fa' => 'ایران'], 'fars' => ['fa' => 'فارس'], 'me' => ['fa' => 'خاورمیانه']],
            'params' => [], 'items' => [], 'done' => 0, 'expected' => 0, 'cats' => jload("$NWP/cats.json", []), 'scales' => jload("$NWP/scales.json", (object)[])];
    foreach (glob("$NWP/*/state.json") ?: [] as $f) {
        $S = jload($f, null); if (!$S || !$S['run']) continue; $m = basename(dirname($f));
        $man['models'][$m] = ['fa' => $S['label'] ?? $m, 'name' => $S['name'] ?? $m, 'run' => $S['run'], 'status' => $S['status'] ?? 'downloading', 'done' => count($S['items']), 'info' => $S['info'] ?? (object)[]];
        if ($man['run'] === null || $S['run'] > $man['run']) $man['run'] = $S['run'];
        if (($S['status'] ?? '') !== 'complete') $man['status'] = 'downloading';
        foreach ($S['items'] as $file => $i) $man['items'][] = ['f' => "data/nwp/$m/{$S['run']}/$file", 'r' => $i['r'], 'm' => $m, 'p' => $i['p'], 's' => (int)$i['s'], 'v' => $i['v'], 'run' => $S['run']];
    }
    foreach ($P as $id => $p) $man['params'][] = ['id' => $id, 'fa' => $p['fa'] ?? $id, 'unit' => $p['unit'] ?? '', 'cat' => $p['cat'] ?? '', 'o' => $p['o'] ?? 0, 'sc' => $p['sc'] ?? '', 'mu' => $p['mu'] ?? '', 'alt' => $p['alt'] ?? '', 'me' => $p['me'] ?? 0, 'ln' => $p['ln'] ?? 0];
    $man['done'] = count($man['items']); $man['expected'] = $man['done'];
    jsave("$DATA/manifest.json", $man);
}

if ($a === 'begin') {
    if ($S['run'] !== $run) {
        if ($S['run'] && $run < $S['run']) jout(['error' => 'older run', 'run' => $S['run']], 409);
        foreach (glob("$MD/20*", GLOB_ONLYDIR) ?: [] as $d) { rrmdir($d); logl("حذف $model/" . basename($d)); }
        $S = ['run' => $run, 'name' => (string)($body['name'] ?? $model), 'label' => (string)($body['label'] ?? $model), 'started' => gmdate('c'), 'status' => 'downloading', 'items' => []];
        $inf = is_array($body['info'] ?? null) ? $body['info'] : []; $S['info'] = ['tclass' => preg_match('~^(daily|weekly|monthly|seasonal)$~', (string)($inf['tclass'] ?? '')) ? $inf['tclass'] : 'daily',
            'res' => mb_substr((string)($inf['res'] ?? ''), 0, 60), 'cyc' => mb_substr((string)($inf['cyc'] ?? ''), 0, 30), 'prov' => mb_substr((string)($inf['prov'] ?? ''), 0, 60)];
        jsave($SF, $S); logl("شروع $model $run");
        if (!empty($body['params']) && is_array($body['params'])) { $P = jload("$NWP/params.json", []); foreach ($body['params'] as $id => $p) if (preg_match('~^[a-z0-9]{1,10}$~', (string)$id)) $P[$id] = ['fa' => (string)($p['fa'] ?? $id), 'unit' => (string)($p['unit'] ?? ''),
            'cat' => preg_replace('~[^a-z]~', '', (string)($p['cat'] ?? '')), 'o' => (int)($p['o'] ?? 0), 'sc' => preg_replace('~[^a-z0-9_]~', '', (string)($p['sc'] ?? '')), 'mu' => (string)($p['mu'] ?? ($p['unit'] ?? '')),
            'alt' => preg_replace('~[^a-z0-9]~', '', (string)($p['alt'] ?? '')), 'me' => (int)($p['me'] ?? 0), 'ln' => (int)($p['ln'] ?? 0)]; jsave("$NWP/params.json", $P); }
        if (!empty($body['cats']) && is_array($body['cats'])) { $Cc = []; foreach ($body['cats'] as $c) if (isset($c['id'])) $Cc[] = ['id' => preg_replace('~[^a-z]~', '', (string)$c['id']), 'fa' => mb_substr((string)($c['fa'] ?? ''), 0, 60), 'o' => (int)($c['o'] ?? 0)]; jsave("$NWP/cats.json", $Cc); }
        if (!empty($body['scales']) && is_array($body['scales'])) { $Sc = jload("$NWP/scales.json", []); foreach ($body['scales'] as $k => $v) if (preg_match('~^[a-z0-9_]{2,16}$~', (string)$k) && is_array($v['c'] ?? null) && is_array($v['e'] ?? null)) $Sc[$k] = $v; jsave("$NWP/scales.json", $Sc); }
        manifest();
    }
    jout(['run' => $S['run'], 'have' => array_keys($S['items'])]);
}
if ($a === 'put') {
    if ($run !== $S['run']) jout(['error' => 'run mismatch', 'run' => $S['run']], 409);
    $file = (string)($body['file'] ?? '');
    if (!preg_match('~^(iran|fars|me)/[a-zA-Z0-9]{1,11}_20\d{8}\.(png|webp)$~', $file)) jout(['error' => 'file'], 400);
    if (empty($_FILES['png']['tmp_name']) || !is_uploaded_file($_FILES['png']['tmp_name'])) jout(['error' => 'no file'], 400);
    $b = (string)file_get_contents($_FILES['png']['tmp_name']);
    $isw = substr($file, -5) === '.webp';
    if ($isw ? (strlen($b) < 100 || substr($b, 0, 4) !== 'RIFF' || substr($b, 8, 4) !== 'WEBP') : (strlen($b) < 5000 || substr($b, 0, 8) !== "\x89PNG\r\n\x1a\n")) jout(['error' => 'bad image'], 400);
    $meta = json_decode((string)($body['meta'] ?? '{}'), true) ?: [];
    [$reg, $nm] = explode('/', $file); [$p, $v] = explode('_', preg_replace('~\.(png|webp)$~', '', $nm));
    $dst = "$MD/$run/$file"; @mkdir(dirname($dst), 0755, true); file_put_contents("$dst.tmp", $b); rename("$dst.tmp", $dst);
    if (substr($p, -1) === 'L') jout(['ok' => true, 'line' => true]);          // لایهٔ خطوطِ کاشی؛ در فهرست جدا نمی‌آید
    $S['items'][$file] = ['r' => $reg, 'p' => $p, 'v' => $v, 's' => (int)($meta['s'] ?? 0)];
    jsave($SF, $S);
    // فهرست کلی هر ۲۰ نقشه (و در پایان) بازسازی می‌شود تا هاست کند نشود
    if (count($S['items']) % 20 === 1) manifest();
    jout(['ok' => true, 'done' => count($S['items'])]);
}
if ($a === 'sync') { manifest(); jout(['ok' => true]); }
if ($a === 'end') {
    if ($run === $S['run']) { $S['status'] = 'complete'; $S['ended'] = gmdate('c'); jsave($SF, $S); logl("پایان $model $run — " . count($S['items']) . ' نقشه'); }
    manifest(); jout(['ok' => true]);
}
jout(['error' => 'bad action'], 400);
