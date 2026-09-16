// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic'; // Always run dynamically, never cache

/**
 * Health check endpoint for deployment verification.
 *
 * The deploy workflow polls this endpoint after pushing new code,
 * comparing `version.commit` against the expected SHA to confirm
 * the new build is live — no fixed sleep required.
 *
 * Azure App Service also uses this path for its built-in health check
 * probe (configured in Bicep via `healthCheckPath: '/api/health'`).
 */
export async function GET() {
  const healthData = {
    status: 'healthy',
    timestamp: new Date().toISOString(),
    version: {
      commit: process.env.NEXT_PUBLIC_GIT_SHA || 'unknown',
      buildTime: process.env.NEXT_PUBLIC_BUILD_TIME || 'unknown',
      branch: process.env.NEXT_PUBLIC_GIT_BRANCH || 'unknown',
    },
    environment: {
      nodeVersion: process.version,
      nextVersion: process.env.npm_package_version || 'unknown',
      platform: process.platform,
      isProduction: process.env.NODE_ENV === 'production',
    },
    deployment: {
      deploymentId: process.env.NEXT_PUBLIC_DEPLOYMENT_ID || 'unknown',
      deployedAt: process.env.NEXT_PUBLIC_DEPLOYED_AT || 'unknown',
    },
  };

  return NextResponse.json(healthData, {
    status: 200,
    headers: {
      'Cache-Control': 'no-store, max-age=0',
    },
  });
}
