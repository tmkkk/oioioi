import six
from django.contrib.auth.models import User
from django.db import models
from django.utils.encoding import python_2_unicode_compatible
from django.utils.translation import ugettext_lazy as _

from oioioi.base.utils.deps import check_django_app_dependencies

check_django_app_dependencies(__name__, ['oioioi.contestexcl'])


@python_2_unicode_compatible
class IpToUser(models.Model):
    """Represents mapping for automatic authorization based on IP address."""

    ip_addr = models.GenericIPAddressField(
        unique=True, unpack_ipv4=True, verbose_name=_("IP address")
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta(object):
        verbose_name = _("IP autoauth mapping")
        verbose_name_plural = _("IP autoauth mappings")

    def __str__(self):
        return six.text_type(self.ip_addr)


@python_2_unicode_compatible
class DnsToUser(models.Model):
    """Represents mapping for automatic authorization based on DNS hostname."""

    dns_name = models.CharField(
        unique=True, max_length=255, verbose_name=_("DNS hostname")
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta(object):
        verbose_name = _("DNS autoauth mapping")
        verbose_name_plural = _("DNS autoauth mappings")

    def __str__(self):
        return six.text_type(self.dns_name)
