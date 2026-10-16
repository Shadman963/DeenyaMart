"""Views for checkout, order confirmation, and public order tracking."""
from decimal import Decimal
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from apps.cart.services import CartService
from apps.shipping.models import ShippingZone
from .models import Order, OrderItem


class CheckoutView(View):
    """Cash on Delivery checkout view with transparent Bangladesh shipping calculation."""

    def get(self, request):
        cart = CartService.get_or_create_cart(request, create=False)
        summary = CartService.get_summary(cart)
        zones = list(ShippingZone.objects.filter(is_active=True))
        default_zone = zones[0] if zones else None
        return render(
            request,
            "orders/checkout.html",
            {
                "cart_summary": summary,
                "shipping_zones": zones,
                "default_zone": default_zone,
            },
        )

    def post(self, request):
        cart = CartService.get_or_create_cart(request, create=False)
        summary = CartService.get_summary(cart)
        if not summary["items"]:
            messages.error(request, "Your shopping bag is empty.")
            return redirect("catalog:product_list")

        customer_name = (request.POST.get("customer_name") or "").strip()
        customer_phone = (request.POST.get("customer_phone") or "").strip()
        shipping_address = (request.POST.get("shipping_address") or "").strip()
        shipping_district = (request.POST.get("shipping_district") or "Dhaka").strip()
        zone_id = request.POST.get("shipping_zone_id")

        if not customer_name or not customer_phone or not shipping_address:
            messages.error(request, "Please fill in your name, mobile number, and delivery address.")
            return redirect("orders:checkout")

        zone = ShippingZone.objects.filter(pk=zone_id, is_active=True).first()
        if not zone:
            zone = ShippingZone.objects.filter(is_active=True).first()

        shipping_fee = zone.base_rate if zone else Decimal("60.00")
        subtotal = summary["subtotal"]
        grand_total = subtotal + shipping_fee

        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        order_number = f"DM-{timestamp}"

        with transaction.atomic():
            order = Order.objects.create(
                order_number=order_number,
                user=request.user if request.user.is_authenticated else None,
                customer_name=customer_name,
                customer_phone=customer_phone,
                shipping_address_line=shipping_address,
                shipping_district=shipping_district,
                shipping_zone=zone,
                subtotal_amount=subtotal,
                shipping_amount=shipping_fee,
                grand_total=grand_total,
                status="CONFIRMED",
                payment_method="COD",
            )
            for item in summary["items"]:
                OrderItem.objects.create(
                    order=order,
                    product=item.variant.product,
                    variant=item.variant,
                    product_name_snapshot=item.variant.product.name,
                    variant_label_snapshot=item.variant.display_label,
                    sku_snapshot=item.variant.sku,
                    unit_retail_price=item.unit_price,
                    quantity=item.quantity,
                    line_total=item.line_total,
                )
            cart.items.all().delete()

        messages.success(request, f"Order {order.order_number} confirmed for Cash on Delivery!")
        return redirect("orders:track_detail", order_number=order.order_number)


class OrderTrackView(View):
    """Public order status lookup (/track-order/)."""

    def get(self, request, order_number=None):
        query_order = (order_number or request.GET.get("order_number") or "").strip()
        order = None
        if query_order:
            order = Order.objects.prefetch_related("items").filter(order_number__iexact=query_order).first()
        return render(
            request,
            "orders/track_order.html",
            {
                "query_order": query_order,
                "order": order,
            },
        )
