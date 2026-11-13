import { App, CfnOutput, RemovalPolicy, Stack } from "aws-cdk-lib";
import { Architecture, Code, LayerVersion, Runtime } from "aws-cdk-lib/aws-lambda";

const app = new App();
const account = "543458927140";
const region = "us-west-2";
const revision = process.env.NENG_RELEASE_SHA;
if (!revision || !/^[0-9a-f]{40}$/.test(revision)) throw new Error("Set NENG_RELEASE_SHA to the checked source SHA");

const stack = new Stack(app, "neng-middleware-dev", { env: { account, region } });
const layer = new LayerVersion(stack, "Middleware", {
  layerVersionName: stack.stackName,
  description: `neng-middleware dev revision ${revision}`,
  code: Code.fromAsset(".layer"),
  compatibleRuntimes: [Runtime.NODEJS_24_X],
  compatibleArchitectures: [Architecture.ARM_64, Architecture.X86_64],
  removalPolicy: RemovalPolicy.RETAIN,
});
new CfnOutput(stack, "LayerVersionArn", { value: layer.layerVersionArn });
