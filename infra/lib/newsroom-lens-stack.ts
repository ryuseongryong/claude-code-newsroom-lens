import * as cdk from 'aws-cdk-lib/core';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecr_assets from 'aws-cdk-lib/aws-ecr-assets';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';
import * as path from 'node:path';

export interface NewsroomLensStackProps extends cdk.StackProps {
  /**
   * CloudFront 가 오리진에 붙이고 ALB 가 검사하는 공유 비밀.
   * ALB 리스너 규칙 조건은 CloudFormation 동적 참조({{resolve:secretsmanager:...}})를
   * 지원하지 않는다 — 조건 값은 합성 시점에 평문이어야 한다. 그래서 이 값만은
   * Secrets Manager 가 아니라 배포하는 사람이 넘긴다(bin/newsroom-lens.ts 참고).
   */
  readonly originVerifyToken: string;
  /** 런타임에 주입할 Bedrock Bearer 토큰 시크릿 이름. 값은 CDK 가 절대 보지 않는다. */
  readonly bedrockSecretName: string;
}

/**
 * CloudFront → ALB → Fargate.
 *
 * ALB 를 두 겹으로 막는다. 어느 한쪽만으로는 부족하다.
 *   1) 보안 그룹: CloudFront 관리형 프리픽스 리스트에서 오는 트래픽만 허용.
 *      → 인터넷에서 ALB DNS 를 직접 때리는 경로를 네트워크 계층에서 끊는다.
 *   2) X-Origin-Verify 헤더: 값이 맞지 않으면 리스너가 403 을 낸다.
 *      → 다른 사람의 CloudFront 배포를 경유해 들어오는 경로를 끊는다.
 *        프리픽스 리스트는 '모든 CloudFront' 를 허용하므로 1)만으로는 이게 막히지 않는다.
 */
export class NewsroomLensStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: NewsroomLensStackProps) {
    super(scope, id, props);

    const repoRoot = path.join(__dirname, '..', '..');

    // ── 네트워크 ──────────────────────────────────────────────────────────
    // NAT 게이트웨이를 두지 않는다(natGateways: 0). 태스크는 퍼블릭 서브넷에 두고
    // 공인 IP 를 붙여 RSS·Bedrock 으로 나간다. 인바운드는 보안 그룹이 ALB 에서 오는
    // 것만 허용하므로 외부에서 태스크로 들어올 길은 없다.
    // NAT 를 쓰면 프라이빗 서브넷에 넣을 수 있지만 AZ 당 월 $35 가 붙는다 — 이
    // 워크로드(읽기 전용 수집기)에는 정당화되지 않는 비용이다.
    const vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        { name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
      ],
      restrictDefaultSecurityGroup: true,
    });

    // ── 보안 그룹 ─────────────────────────────────────────────────────────
    // description 은 ASCII 여야 한다. CloudFormation 이 GroupDescription 에
    // 한글을 거부한다(패턴 검증 F3031). 설명은 주석으로 남긴다.
    const albSg = new ec2.SecurityGroup(this, 'AlbSg', {
      vpc,
      description: 'ALB - allow only CloudFront origin-facing prefix list',
      allowAllOutbound: true,
    });

    // 리전별 CloudFront origin-facing 프리픽스 리스트. 하드코딩된 ID 를 쓰지 않고
    // 조회한다 — ID 는 리전마다 다르고 AWS 가 바꿀 수 있다.
    const cloudFrontPrefixList = ec2.PrefixList.fromLookup(this, 'CloudFrontPrefixList', {
      prefixListName: 'com.amazonaws.global.cloudfront.origin-facing',
    });
    albSg.addIngressRule(
      ec2.Peer.prefixList(cloudFrontPrefixList.prefixListId),
      ec2.Port.tcp(80),
      // 규칙 description 은 ASCII 부분집합만 받는다: a-zA-Z0-9. _-:/()#,@[]+=&;{}!$*
      // 화살표를 쓸 수 없다. 유니코드 화살표는 물론이고 '->' 도 '>' 가 집합에 없어서
      // 400 으로 실패한다(GroupDescription 과 별개로 AuthorizeSecurityGroupIngress
      // 에서 걸리므로 두 번 넘어졌다). 방향은 말로 쓴다.
      'Ingress from CloudFront origin-facing prefix list',
    );

    const serviceSg = new ec2.SecurityGroup(this, 'ServiceSg', {
      vpc,
      description: 'Fargate task - allow port 8000 from ALB only',
      allowAllOutbound: true, // RSS 4곳 + Bedrock 으로 나가야 한다
    });
    serviceSg.addIngressRule(albSg, ec2.Port.tcp(8000), 'Ingress from ALB security group');

    // ── 컨테이너 ──────────────────────────────────────────────────────────
    const cluster = new ecs.Cluster(this, 'Cluster', {
      vpc,
      containerInsightsV2: ecs.ContainerInsights.DISABLED,
    });

    // 빌드 컨텍스트는 저장소 루트다. Dockerfile 이 backend/ 와 frontend/ 를 모두 넣는다.
    const image = new ecr_assets.DockerImageAsset(this, 'Image', {
      directory: repoRoot,
      file: 'backend/Dockerfile',
      platform: ecr_assets.Platform.LINUX_ARM64,
      exclude: [
        'infra/cdk.out', 'infra/node_modules', 'node_modules',
        'backend/.venv', 'backend/tests', 'tools', 'docs', '**/__pycache__',
      ],
    });

    // 값이 아니라 '이름' 으로 참조한다. 토큰은 이 저장소에도, 템플릿에도, 이미지에도
    // 들어가지 않는다. 배포 전에 CLI 로 미리 만들어 둔다.
    const bedrockSecret = secretsmanager.Secret.fromSecretNameV2(
      this, 'BedrockToken', props.bedrockSecretName,
    );

    const taskDef = new ecs.FargateTaskDefinition(this, 'TaskDef', {
      cpu: 512,
      memoryLimitMiB: 1024,
      // 이 이미지는 Graviton 빌드 머신에서 만들어졌다. ARM64 를 명시하지 않으면
      // 태스크가 X86_64 로 뜨면서 'exec format error' 로 조용히 죽는다.
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.ARM64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });

    taskDef.addContainer('app', {
      image: ecs.ContainerImage.fromDockerImageAsset(image),
      portMappings: [{ containerPort: 8000, protocol: ecs.Protocol.TCP }],
      environment: {
        BEDROCK_REGION: this.region,
        POLL_INTERVAL_SECONDS: '120',
        LOG_LEVEL: 'INFO',
      },
      // 기동 시점에 주입된다. 태스크 정의에는 시크릿 ARN 만 남는다.
      secrets: {
        AWS_BEARER_TOKEN_BEDROCK: ecs.Secret.fromSecretsManager(bedrockSecret),
      },
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'newsroom-lens',
        logRetention: logs.RetentionDays.ONE_WEEK,
      }),
      stopTimeout: cdk.Duration.seconds(15),
    });

    const service = new ecs.FargateService(this, 'Service', {
      cluster,
      taskDefinition: taskDef,
      desiredCount: 1,
      // NAT 가 없으므로 이미지 pull 과 외부 수집을 위해 공인 IP 가 필요하다.
      assignPublicIp: true,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      securityGroups: [serviceSg],
      // 첫 수집 전에도 /healthz 는 200 을 주므로 길게 줄 필요가 없다.
      healthCheckGracePeriod: cdk.Duration.seconds(30),
      circuitBreaker: { rollback: true },
      minHealthyPercent: 0, // 태스크 1개 — 교체 시 잠깐 끊기는 것을 허용한다
    });

    // ── ALB ──────────────────────────────────────────────────────────────
    const alb = new elbv2.ApplicationLoadBalancer(this, 'Alb', {
      vpc,
      internetFacing: true,
      securityGroup: albSg,
      idleTimeout: cdk.Duration.seconds(120), // Bedrock 왕복이 이 밑으로 끝나야 한다
    });

    const targetGroup = new elbv2.ApplicationTargetGroup(this, 'Tg', {
      vpc,
      port: 8000,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targetType: elbv2.TargetType.IP,
      targets: [service],
      deregistrationDelay: cdk.Duration.seconds(10),
      healthCheck: {
        path: '/healthz',
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        healthyThresholdCount: 2,
        unhealthyThresholdCount: 3,
      },
    });

    const listener = alb.addListener('Http', {
      port: 80,
      protocol: elbv2.ApplicationProtocol.HTTP,
      // open: false 가 반드시 필요하다. 기본값 true 는 보안 그룹에 0.0.0.0/0 인그레스를
      // 조용히 추가하는데, 그러면 위에서 붙인 CloudFront 프리픽스 리스트 규칙이 나란히
      // 남아 있으면서도 아무 의미가 없어진다(넓은 규칙이 이긴다). 합성된 템플릿의
      // SecurityGroupIngress 를 직접 확인하기 전까지는 코드만 보고는 알 수 없다.
      open: false,
      // 기본 동작이 '거부' 다. 헤더가 맞는 요청만 아래 규칙으로 통과한다.
      //
      // contentType 에 charset 파라미터를 붙일 수 없다. ALB 는 정확히
      // 'text/html' | 'application/json' | 'application/javascript' | 'text/css' |
      // 'text/plain' 만 받고 'text/plain; charset=utf-8' 은 400 이다. charset 을
      // 지정할 수 없으므로 본문도 ASCII 로 쓴다 — 어차피 이 응답은 ALB DNS 를 직접
      // 찔러 본 사람만 보는 기계용 메시지다.
      defaultAction: elbv2.ListenerAction.fixedResponse(403, {
        contentType: 'text/plain',
        messageBody: 'Forbidden: reach this service through its CloudFront distribution.',
      }),
    });

    listener.addAction('ViaCloudFront', {
      priority: 10,
      conditions: [
        elbv2.ListenerCondition.httpHeader('X-Origin-Verify', [props.originVerifyToken]),
      ],
      action: elbv2.ListenerAction.forward([targetGroup]),
    });

    // ── CloudFront ───────────────────────────────────────────────────────
    const origin = new origins.LoadBalancerV2Origin(alb, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
      httpPort: 80,
      customHeaders: { 'X-Origin-Verify': props.originVerifyToken },
      readTimeout: cdk.Duration.seconds(60),
      keepaliveTimeout: cdk.Duration.seconds(60),
    });

    // SPA 셸과 정적 자산은 짧게 캐시한다. 기사 데이터는 전부 /api/* 로 나가므로
    // 여기서 캐시해도 화면이 늙지 않는다.
    const shellCachePolicy = new cloudfront.CachePolicy(this, 'ShellCache', {
      comment: 'Newsroom Lens - 정적 셸 (짧은 TTL)',
      minTtl: cdk.Duration.seconds(0),
      defaultTtl: cdk.Duration.seconds(60),
      maxTtl: cdk.Duration.seconds(300),
      enableAcceptEncodingGzip: true,
      enableAcceptEncodingBrotli: true,
    });

    const distribution = new cloudfront.Distribution(this, 'Cdn', {
      comment: 'Newsroom Lens — BBC/Guardian/NHK/연합뉴스 관점 비교',
      defaultRootObject: 'index.html',
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      priceClass: cloudfront.PriceClass.PRICE_CLASS_200,
      defaultBehavior: {
        origin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
        cachePolicy: shellCachePolicy,
        compress: true,
      },
      additionalBehaviors: {
        // API 는 절대 캐시하지 않는다. /api/lens 는 POST 라 ALLOW_ALL 이 필요하다.
        '/api/*': {
          origin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
          compress: true,
        },
        '/healthz': {
          origin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        },
      },
    });

    // ── 출력 ─────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'SiteUrl', {
      value: `https://${distribution.distributionDomainName}/`,
      description: '공유용 공개 URL',
    });
    new cdk.CfnOutput(this, 'AlbDnsName', {
      value: alb.loadBalancerDnsName,
      description: 'ALB DNS (직접 접근 시 403 이어야 정상)',
    });
    new cdk.CfnOutput(this, 'BedrockSecretName', { value: props.bedrockSecretName });
  }
}
