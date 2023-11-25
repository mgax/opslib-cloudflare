import json
from base64 import b64encode
from typing import cast

from opslib import Component, Lazy, Prop, evaluate, lazy_property
from opslib.cli import ComponentGroup
from opslib.terraform import TerraformProvider

from .random_secret import RandomSecret


class Cloudflare(Component):
    def build(self):
        self.provider = TerraformProvider(
            name="cloudflare",
            source="cloudflare/cloudflare",
            version="~> 4.0",
        )

        self.accounts = self.provider.data(
            type="cloudflare_accounts",
            output=["accounts"],
        )

    def account(self, name, **kwargs):
        def get_account_id():
            for account in cast(list, evaluate(self.accounts.output["accounts"])):
                if account["name"] == name:
                    return account["id"]

        return CloudflareAccount(
            cloudflare=self,
            name=name,
            account_id=Lazy(get_account_id),
            **kwargs,
        )

    def add_commands(self, cli: ComponentGroup):
        @cli.command()
        def accounts():
            for account in cast(list, evaluate(self.accounts.output["accounts"])):
                print(account["id"], account["name"])


class CloudflareAccount(Component):
    class Props:
        cloudflare = Prop(Cloudflare)
        name = Prop(str)
        account_id = Prop(str, lazy=True)

    def build(self):
        self.zones = self.props.cloudflare.provider.data(
            type="cloudflare_zones",
            args={
                "filter": {
                    "account_id": self.props.account_id,
                },
            },
            output=["zones"],
        )

    def add_commands(self, cli: ComponentGroup):
        @cli.command()
        def zones():
            for zone in cast(list, evaluate(self.zones.output["zones"])):
                print(zone["id"], zone["name"])

    def zone(self, name, **kwargs):
        def get_zone_id():
            for zone in cast(list, evaluate(self.zones.output["zones"])):
                if zone["name"] == name:
                    return zone["id"]

        return CloudflareZone(
            cloudflare=self.props.cloudflare,
            name=name,
            zone_id=Lazy(get_zone_id),
            **kwargs,
        )

    def tunnel(self, **kwargs):
        return CloudflareTunnel(
            cloudflare=self.props.cloudflare,
            account_id=self.props.account_id,
            **kwargs,
        )


class CloudflareZone(Component):
    class Props:
        cloudflare = Prop(Cloudflare)
        name = Prop(str)
        zone_id = Prop(str, lazy=True)

    @property
    def zone_id(self):
        return self.props.zone_id

    def record(self, **kwargs):
        return CloudflareRecord(
            cloudflare=self.props.cloudflare,
            zone=self,
            **kwargs,
        )


class CloudflareRecord(Component):
    class Props:
        cloudflare = Prop(Cloudflare)
        zone = Prop(CloudflareZone)
        args = Prop(dict)

    def build(self):
        self.record = self.props.cloudflare.provider.resource(
            type="cloudflare_record",
            args=dict(
                zone_id=self.props.zone.zone_id,
                **self.props.args,
            ),
        )


class CloudflareTunnel(Component):
    class Props:
        cloudflare = Prop(Cloudflare)
        account_id = Prop(str, lazy=True)
        name = Prop(str)
        secret = Prop(str | None, lazy=True)

    def build(self):
        if self.props.secret is None:
            self.secret = RandomSecret()

        self.tunnel = self.props.cloudflare.provider.resource(
            type="cloudflare_tunnel",
            args=dict(
                account_id=self.props.account_id,
                name=self.props.name,
                secret=self._secret,
            ),
            output=["id"],
        )

    @lazy_property
    def _secret(self):
        if self.props.secret:
            return self.props.secret

        else:
            value = cast(str, evaluate(self.secret.value))
            return b64encode(value.encode("utf8")).decode("utf8")

    @lazy_property
    def cloudflared_token(self):
        payload = {
            "a": evaluate(self.props.account_id),
            "t": evaluate(self.tunnel.output["id"]),
            "s": evaluate(self._secret),
        }
        return b64encode(json.dumps(payload).encode("utf8")).decode("utf8")

    @lazy_property
    def cname_value(self):
        return f"{evaluate(self.tunnel.output['id'])}.cfargotunnel.com"

    def cname_record(self, zone, name):
        return zone.record(
            args=dict(
                name=name,
                type="CNAME",
                value=self.cname_value,
                proxied=True,
            ),
        )
