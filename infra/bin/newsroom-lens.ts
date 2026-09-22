#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { randomBytes } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync, chmodSync } from 'node:fs';
import * as path from 'node:path';
import { NewsroomLensStack } from '../lib/newsroom-lens-stack';

const app = new cdk.App();

/**
 * X-Origin-Verify 값을 구한다.
 *
 * ALB 리스너 규칙의 조건 값은 합성 시점에 평문이어야 해서(동적 참조 불가) 어딘가에
 * 두고 재사용해야 한다. 매번 새로 만들면 배포마다 값이 바뀌어 스택이 무의미하게
 * 갱신된다. 그래서 순서를 정해 둔다.
 *   1) ORIGIN_VERIFY_TOKEN 환경변수 (CI 에서 쓰는 경로)
 *   2) infra/.origin-verify 파일 (로컬 재배포 시 값 유지)
 *   3) 둘 다 없으면 새로 만들어 2)에 0600 으로 저장
 * 이 파일은 .gitignore 에 있다.
 */
function resolveOriginVerifyToken(): string {
  const fromEnv = process.env.ORIGIN_VERIFY_TOKEN?.trim();
  if (fromEnv) return fromEnv;

  const file = path.join(__dirname, '..', '.origin-verify');
  if (existsSync(file)) {
    const saved = readFileSync(file, 'utf8').trim();
    if (saved) return saved;
  }

  const generated = randomBytes(24).toString('base64url');
  writeFileSync(file, generated + '\n', { mode: 0o600 });
  chmodSync(file, 0o600);
  console.log(`X-Origin-Verify 값을 새로 만들어 ${file} 에 저장했습니다.`);
  return generated;
}

new NewsroomLensStack(app, 'NewsroomLensStack', {
  description: 'Newsroom Lens — CloudFront → ALB → Fargate (Capstone 6)',
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    // PrefixList.fromLookup 은 환경이 확정되어야 한다 (env-agnostic 스택에서는 실패).
    region: process.env.CDK_DEFAULT_REGION ?? 'ap-northeast-2',
  },
  originVerifyToken: resolveOriginVerifyToken(),
  bedrockSecretName: process.env.BEDROCK_SECRET_NAME ?? 'newsroom-lens/bedrock-bearer-token',
  tags: {
    Project: 'newsroom-lens',
    Capstone: '6',
  },
});
