from django.contrib import admin
from .models import SimulatorFactorSet, CoordinatorStoreAccess, SniperAssignment


@admin.register(SimulatorFactorSet)
class SimulatorFactorSetAdmin(admin.ModelAdmin):
    list_display = ('role', 'updated_at', 'updated_by')
    search_fields = ('role',)


@admin.register(CoordinatorStoreAccess)
class CoordinatorStoreAccessAdmin(admin.ModelAdmin):
    list_display = ('coordinator', 'updated_at', 'updated_by')
    search_fields = ('coordinator__first_name', 'coordinator__last_name', 'coordinator__email')
    filter_horizontal = ('sectors',)


@admin.register(SniperAssignment)
class SniperAssignmentAdmin(admin.ModelAdmin):
    list_display = ('sniper', 'coordinator', 'updated_at', 'updated_by')
    search_fields = (
        'sniper__first_name', 'sniper__last_name', 'sniper__email',
        'coordinator__first_name', 'coordinator__last_name', 'coordinator__email',
    )
    autocomplete_fields = ('sniper', 'coordinator')
