from dataclasses import replace

import pytest

from neng_middleware.settings import AuthSettings, assert_safe_boot


def configured(boundary, monkeypatch):
    monkeypatch.delenv('AWS_LAMBDA_FUNCTION_NAME', raising=False)
    monkeypatch.delenv('NENG_DEPLOYMENT_MODE', raising=False)
    if boundary == 'lambda':
        monkeypatch.setenv('AWS_LAMBDA_FUNCTION_NAME', 'synthetic-hosted-dev')
    if boundary == 'gateway':
        monkeypatch.setenv('NENG_DEPLOYMENT_MODE', 'gateway')
    return AuthSettings(jwks_url='https://example.test/jwks', issuer='https://example.test/auth',
        audience='authenticated', algorithms=('ES256',), env='prod' if boundary == 'prod' else 'dev',
        disabled=False)


@pytest.mark.parametrize('boundary', ['lambda', 'gateway', 'prod'])
@pytest.mark.parametrize('invalid', [{'disabled': True}, {'issuer': ''}, {'audience': ''}])
def test_hosted_boot_rejects_bypass_and_incomplete_verification(boundary, invalid, monkeypatch):
    settings = replace(configured(boundary, monkeypatch), **invalid)
    with pytest.raises(RuntimeError, match='bypass'):
        assert_safe_boot(settings)


@pytest.mark.parametrize('boundary', ['lambda', 'gateway', 'prod'])
def test_configured_hosted_boot_keeps_jwt_verification(boundary, monkeypatch):
    assert_safe_boot(configured(boundary, monkeypatch))
