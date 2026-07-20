// =============================================================================
// @file  run_pure_tests.mjs
// @brief ui-kit 纯函数测试(H09 §二 I.3:fetch 封装四态错误文案含等待时长)。
//        经 node --experimental-strip-types 直载 TS 源;由 tests/test_i_frontend.py
//        子进程调用并入 unittest 全量回归。断言失败即非零退出。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
/* global console */
import assert from 'node:assert/strict';
import {
  classifyError,
  isSafeNext,
  buildLoginRedirect,
  parseWaitSeconds,
  formatWait,
} from '../src/errors.ts';
import { parseEnvelope, parseFieldErrors, maskPhone, makeRequestId } from '../src/envelope.ts';
import { matchPath } from '../src/route_match.ts';

// —— 四态错误文案(06-E8) ——
const e401 = classifyError(401, {}, null, '/wrongbook?page=2');
assert.equal(e401.kind, 'unauthorized');
assert.ok(e401.text.includes('登录'));
assert.equal(e401.loginRedirect, '/login?next=%2Fwrongbook%3Fpage%3D2'); // 保留站内 next

const e403 = classifyError(403, {}, null, '/');
assert.equal(e403.kind, 'forbidden');
assert.ok(e403.text.includes('权限'));
assert.equal(e403.loginRedirect, null);
assert.notEqual(e401.text, e403.text); // 401/403 文案区分

const e423 = classifyError(423, {}, { error: '账号已锁定,请 15 分钟后再试' }, '/');
assert.equal(e423.kind, 'locked');
assert.equal(e423.waitSeconds, 900); // 中文文案提取等待时长
assert.ok(e423.text.includes('锁定') && e423.text.includes('15 分钟'));

const e429 = classifyError(429, { 'retry-after': '30' }, {}, '/');
assert.equal(e429.kind, 'ratelimited');
assert.equal(e429.waitSeconds, 30); // Retry-After 头优先
assert.ok(e429.text.includes('限速') && e429.text.includes('30 秒'));
assert.notEqual(e423.text, e429.text); // 423/429 文案区分

// —— 回跳安全(06-E13):next 仅站内相对路径 ——
assert.equal(isSafeNext('/portal'), true);
assert.equal(isSafeNext('//evil.example/x'), false);
assert.equal(isSafeNext('https://evil.example'), false);
assert.equal(isSafeNext('/ok\\..'), false);
assert.equal(buildLoginRedirect('/login', 'https://evil.example'), '/login'); // 不安全 next 丢弃

// —— 等待时长解析优先级 ——
assert.equal(parseWaitSeconds({ 'retry-after': '5' }, { retry_after: 99 }), 5);
assert.equal(parseWaitSeconds({}, { retry_after: 42 }), 42);
assert.equal(parseWaitSeconds({}, { error: '请 30 秒后再试' }), 30);
assert.equal(parseWaitSeconds({}, {}), null);
assert.equal(formatWait(null), '请稍后再试');
assert.equal(formatWait(59), '请 59 秒后再试');
assert.equal(formatWait(61), '请 2 分钟后再试');

// —— 信封解析(R9) ——
const env = parseEnvelope({
  code: 42204,
  message: '条件必填缺失',
  request_id: 'web-1',
  data: { a: 1 },
});
assert.ok(env && env.code === 42204 && env.requestId === 'web-1' && env.data.a === 1);
assert.equal(parseEnvelope({ hello: 1 }), null);

// —— 字段级校验错误:pydantic detail 与 field_errors 双来源 ——
const fe1 = parseFieldErrors({ detail: [{ loc: ['body', 'altitude'], msg: '高度必填' }] });
assert.equal(fe1['altitude'], '高度必填');
const fe2 = parseFieldErrors({ field_errors: { nvr_report_cron: 'cron 表达式非法' } });
assert.equal(fe2['nvr_report_cron'], 'cron 表达式非法');

// —— 手机号打码(H04 §七) ——
assert.equal(maskPhone('13912345678'), '139****5678');
assert.equal(maskPhone(''), '');
assert.ok(!maskPhone('13912345678').includes('1234')); // 中段不外泄
assert.ok(maskPhone('123456').includes('*'));

// —— X-Request-Id 形态 ——
assert.match(makeRequestId(), /^web-[0-9a-f]+$/);

// —— history 路由匹配 ——
assert.deepEqual(matchPath('/q/:qno', '/q/17'), { qno: '17' });
assert.equal(matchPath('/q/:qno', '/x/17'), null);
assert.deepEqual(matchPath('/admin/*', '/admin/a/b'), {});
assert.equal(matchPath('/a', '/a/b'), null);

console.warn('ui-kit 纯函数测试:全部通过');
