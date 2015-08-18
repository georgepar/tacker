# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2013, 2014 Intel Corporation.
# Copyright 2013, 2014 Isaku Yamahata <isaku.yamahata at intel com>
#                                     <isaku.yamahata at gmail com>
# All Rights Reserved.
#
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# @author: Isaku Yamahata, Intel Corporation.

import eventlet
import inspect
import ast

from oslo_config import cfg
from sqlalchemy.orm import exc as orm_exc

from tacker.api.v1 import attributes
from tacker.common import driver_manager
from tacker.common import topics
from tacker import context as t_context
from tacker.db.vm import proxy_db  # noqa
from tacker.db.vm import vm_db
from tacker.extensions import servicevm
from tacker.openstack.common import excutils
from tacker.openstack.common import log as logging
from tacker.plugins.common import constants
from tacker.vm.mgmt_drivers import constants as mgmt_constants
from tacker.vm import monitor
from tacker.vm import proxy_api

LOG = logging.getLogger(__name__)


class ServiceVMMgmtMixin(object):
    OPTS = [
        cfg.MultiStrOpt(
            'mgmt_driver', default=[],
            help=_('MGMT driver to communicate with '
                   'Hosting Device/logical service '
                   'instance servicevm plugin will use')),
    ]
    cfg.CONF.register_opts(OPTS, 'servicevm')

    def __init__(self):
        super(ServiceVMMgmtMixin, self).__init__()
        self._mgmt_manager = driver_manager.DriverManager(
            'tacker.servicevm.mgmt.drivers', cfg.CONF.servicevm.mgmt_driver)

    def _invoke(self, device_dict, **kwargs):
        method = inspect.stack()[1][3]
        return self._mgmt_manager.invoke(
            self._mgmt_driver_name(device_dict), method, **kwargs)

    def mgmt_create_pre(self, context, device_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict)

    def mgmt_create_post(self, context, device_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict)

    def mgmt_update_pre(self, context, device_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict)

    def mgmt_update_post(self, context, device_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict)

    def mgmt_delete_pre(self, context, device_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict)

    def mgmt_delete_post(self, context, device_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict)

    def mgmt_get_config(self, context, device_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict)

    def mgmt_url(self, context, device_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict)

    def mgmt_call(self, context, device_dict, kwargs):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict,
            kwargs=kwargs)

    def mgmt_service_driver(self, context, device_dict, service_instance_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict,
            service_instance=service_instance_dict)

    def mgmt_service_create_pre(self, context, device_dict,
                                service_instance_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict,
            service_instance=service_instance_dict)

    def mgmt_service_create_post(self, context, device_dict,
                                 service_instance_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict,
            service_instance=service_instance_dict)

    def mgmt_service_update_pre(self, context, device_dict,
                                service_instance_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict,
            service_instance=service_instance_dict)

    def mgmt_service_update_post(self, context, device_dict,
                                 service_instance_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict,
            service_instance=service_instance_dict)

    def mgmt_service_delete_pre(self, context, device_dict,
                                service_instance_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict,
            service_instance=service_instance_dict)

    def mgmt_service_delete_post(self, context, device_dict,
                                 service_instance_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict,
            service_instance=service_instance_dict)

    def mgmt_service_address(self, context, device_dict,
                             service_instance_dict):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict,
            service_instance=service_instance_dict)

    def mgmt_service_call(self, context, device_dict, service_instance_dict,
                          kwargs):
        return self._invoke(
            device_dict, plugin=self, context=context, device=device_dict,
            service_instance=service_instance_dict, kwargs=kwargs)


class ServiceVMPlugin(vm_db.ServiceResourcePluginDb, ServiceVMMgmtMixin):
    """ServiceVMPlugin which supports ServiceVM framework
    """
    OPTS = [
        cfg.ListOpt(
            'infra_driver', default=['heat'],
            help=_('Hosting device drivers servicevm plugin will use')),
    ]
    cfg.CONF.register_opts(OPTS, 'servicevm')
    supported_extension_aliases = ['servicevm']

    def __init__(self):
        super(ServiceVMPlugin, self).__init__()
        self._pool = eventlet.GreenPool()
        self._device_manager = driver_manager.DriverManager(
            'tacker.servicevm.device.drivers',
            cfg.CONF.servicevm.infra_driver)
        self.proxy_api = proxy_api.ServiceVMPluginApi(topics.SERVICEVM_AGENT)
        self._device_status = monitor.DeviceStatus()
        self.sff_counter = 1

    def spawn_n(self, function, *args, **kwargs):
        self._pool.spawn_n(function, *args, **kwargs)

    ###########################################################################
    # hosting device template

    def create_device_template(self, context, device_template):
        template = device_template['device_template']
        LOG.debug(_('template %s'), template)

        infra_driver = template.get('infra_driver')
        if not attributes.is_attr_set(infra_driver):
            LOG.debug(_('hosting device driver must be specified'))
            raise servicevm.InfraDriverNotSpecified()
        if infra_driver not in self._device_manager:
            LOG.debug(_('unknown hosting device driver '
                        '%(infra_driver)s in %(drivers)s'),
                      {'infra_driver': infra_driver,
                       'drivers': cfg.CONF.servicevm.infra_driver})
            raise servicevm.InvalidInfraDriver(infra_driver=infra_driver)

        service_types = template.get('service_types')
        if not attributes.is_attr_set(service_types):
            LOG.debug(_('service type must be specified'))
            raise servicevm.ServiceTypesNotSpecified()
        for service_type in service_types:
            # TODO(yamahata):
            # framework doesn't know what services are valid for now.
            # so doesn't check it here yet.
            pass

        self._device_manager.invoke(
            infra_driver, 'create_device_template_pre', plugin=self,
            context=context, device_template=device_template)

        return super(ServiceVMPlugin, self).create_device_template(
            context, device_template)

    ###########################################################################
    # hosting device

    def add_device_to_monitor(self, device_dict):
        device_id = device_dict['id']
        dev_attrs = device_dict['attributes']
        if dev_attrs.get('monitoring_policy') == 'ping':
            def down_cb(hosting_device_):
                if self._mark_device_dead(device_id):
                    self._device_status.mark_dead(device_id)
                    device_dict_ = self.get_device(
                        t_context.get_admin_context(), device_id)
                    failure_cls = monitor.FailurePolicy.get_policy(
                        device_dict_['attributes'].get('failure_policy'),
                        device_dict_)
                    if failure_cls:
                        failure_cls.on_failure(self, device_dict_)

            hosting_device = self._device_status.to_hosting_device(
                device_dict, down_cb)
            KEY_LIST = ('monitoring_policy', 'failure_policy')
            for key in KEY_LIST:
                if key in dev_attrs:
                    hosting_device[key] = dev_attrs[key]
            self._device_status.add_hosting_device(hosting_device)

    def config_device(self, context, device_dict):
        config = device_dict['attributes'].get('config')
        if not config:
            return
        eventlet.sleep(30)      # wait for vm to be ready
        device_id = device_dict['id']
        update = {
            'device': {
                'id': device_id,
                'attributes': {'config': config},
            }
        }
        self.update_device(context, device_id, update)

    def _create_device_wait(self, context, device_dict):
        driver_name = self._infra_driver_name(device_dict)
        device_id = device_dict['id']
        instance_id = self._instance_id(device_dict)

        try:
            self._device_manager.invoke(
                driver_name, 'create_wait', plugin=self, context=context,
                device_dict=device_dict, device_id=instance_id)
        except servicevm.DeviceCreateWaitFailed:
            instance_id = None
            del device_dict['instance_id']

        if instance_id is None:
            mgmt_url = None
        else:
            # mgmt_url = self.mgmt_url(context, device_dict)
            # FIXME(yamahata):
            mgmt_url = device_dict['mgmt_url']

        self._create_device_post(
            context, device_id, instance_id, mgmt_url, device_dict)
        if instance_id is None:
            self.mgmt_create_post(context, device_dict)
            return

        self.mgmt_create_post(context, device_dict)
        device_dict['mgmt_url'] = mgmt_url

        kwargs = {
            mgmt_constants.KEY_ACTION: mgmt_constants.ACTION_CREATE_DEVICE,
            mgmt_constants.KEY_KWARGS: {'device': device_dict},
        }
        new_status = constants.ACTIVE
        try:
            self.mgmt_call(context, device_dict, kwargs)
        except Exception:
            LOG.exception(_('create_device_wait'))
            new_status = constants.ERROR
        device_dict['status'] = new_status
        self._create_device_status(context, device_id, new_status)

    def _create_device(self, context, device):
        device_dict = self._create_device_pre(context, device)
        device_id = device_dict['id']
        driver_name = self._infra_driver_name(device_dict)
        LOG.debug(_('device_dict %s'), device_dict)
        self.mgmt_create_pre(context, device_dict)
        try:
            instance_id = self._device_manager.invoke(
                driver_name, 'create', plugin=self,
                context=context, device=device_dict)
        except Exception:
            with excutils.save_and_reraise_exception():
                self.delete_device(context, device_id)

        if instance_id is None:
            self._create_device_post(context, device_id, None, None,
                device_dict)
            return

        device_dict['instance_id'] = instance_id
        return device_dict

    @staticmethod
    def find_ovs_br(self, sf_id, network_map):
        """
        :param sf_id: info to ID an sf, for example neutron port id
        :param network_map: ovsdb network topology list
        :return: bridge_dict: br_name, ovs_ip, ovs_port key-values
        """
        # trozet better way to traverse this is to use json lib itself
        # for now this was quicker to write
        bridge_dict = dict()
        for net in network_map:
            if 'node' in net:
                for node_entry in net['node']:
                    if 'termination-point' in node_entry:
                        for endpoint in node_entry['termination-point']:
                            if 'ovsdb:interface-external-ids' in endpoint:
                                for external_id in endpoint['ovsdb:interface-external-ids']:
                                    if 'external-id-value' in external_id:
                                        if external_id['external-id-value'] == sf_id:
                                            print 'Found!'
                                            print node_entry['ovsdb:bridge-name']
                                            bridge_dict['br_name'] = node_entry['ovsdb:bridge-name']
                                            break
                                        else:
                                            print 'Not Found'
                if 'br_name' in bridge_dict:
                    for node_entry in net['node']:
                        if 'ovsdb:connection-info' in node_entry:
                            bridge_dict['ovs_ip'] = node_entry['ovsdb:connection-info']['remote-ip']
                            bridge_dict['ovs_port'] = node_entry['ovsdb:connection-info']['remote-port']
                            break
        if all(key in bridge_dict for key in ('br_name', 'ovs_ip', 'ovs_port')):
            return bridge_dict

        return

    def locate_ovs_to_sf(self, sfs_dict, driver_name):
        """
        :param sfs_dict: dictionary of SFs by id to network id (neutron port id)
        :param driver_name: name of SDN driver
        :return: dictionary mapping sfs to bridge name
        """
        # get topology
        try:
            network = self._device_manager.invoke(
                driver_name, 'list_network_topology')
        except Exception:
            LOG.exception(_('Unable to get network topology'))
            return

        if network is None:
            return

        LOG.debug(_('Network is %s'), network)

        # br_mapping key is nested dict with br_name as first key
        br_mapping = dict()

        # make extensible to other controllers
        if driver_name is 'opendaylight':
            network_map = network['network-topology']['topology']
            # look to see if vm_id exists in network dict
            for sf in sfs_dict:
                br_dict = self.find_ovs_br(sfs_dict[sf], network_map)
                LOG.debug(_('br_dict from find_ovs %s'), br_dict)
                if br_dict is not None:
                    br_name = br_dict['br_name']
                    if br_name in br_mapping:
                        br_mapping[br_name]['sfs'] = [sf]+br_mapping[br_name]['sfs']
                    else:
                        br_mapping[br_name] = dict()
                        br_mapping[br_name]['sfs'] = [sf]
                        br_mapping[br_name]['ovs_ip'] = br_dict['ovs_ip']
                        br_mapping[br_name]['sff_name'] = 'sff' + str(self.sff_counter)
                        self.sff_counter += 1
                else:
                    LOG.debug(_('Could not find OVS bridge for %s'), sf)

        return br_mapping

    def create_sff_json(self, bridge_mapping, sfs_dict):
        """
        Creates JSON request body for ODL SFC
        :param bridge_mapping: dictionary of sf to ovs bridges
        :return: dictionary with formatted fields
        """
        sff_dp_loc = {'name': '',
                      'service-function-forwarder-ovs:ovs-bridge': '',
                      'data-plane-locator':
                          {
                          'transport': 'service-locator:vxlan-gpe',
                          'port': '',
                          'ip': '',
                          },
                      'service-function-forwarder-ovs:ovs-options':
                          {
                          'nshc1': 'flow',
                          'nsp': 'flow',
                          'key': 'flow',
                          'remote-ip': 'flow',
                          'nsi': 'flow',
                          'nshc2': 'flow',
                          'nshc3': 'flow',
                          'dst-port': '',
                          'nshc4': 'flow'
                          }
                      }

        sf_template = {'name': '',
                       'type': '',
                       'sff-sf-dataplane-locator': '',
                       }
        sff_sf_dp_loc = {'service-function-forwarder-ovs:ovs-bridge': '',
                         'transport': 'service-locator:vxlan-gpe',
                         'port': '',
                         'ip': ''
                         }

        sff_list = []

        # build dict for each bridge
        for br in bridge_mapping.keys():
            # create sff dataplane locator
            temp_sff_dp_loc = {x: y for (x, y) in sff_dp_loc}
            temp_sff_dp_loc['name'] = bridge_mapping[br]['sff_name']
            temp_sff_dp_loc['data-plane-locator']['port'] = bridge_mapping[br]['port']
            temp_sff_dp_loc['data-plane-locator']['ip'] = bridge_mapping[br]['ip']
            temp_sff_dp_loc['service-function-forwarder-ovs:ovs-bridge'] = br
            temp_bridge_dict= {'bridge-name': br}
            sf_dicts = list()
            for sf in bridge_mapping[sff]['sfs']:
                # build sf portion of dict
                temp_sf_dict = {x: y for (x, y) in sf_template}
                temp_sf_dict['name'] = sfs_dict[sf]['name']
                temp_sf_dict['type'] = sfs_dict[sf]['type']
                # build sf dataplane locator
                temp_sff_sf_dp_loc = {x: y for (x, y) in sff_sf_dp_loc}
                temp_sff_sf_dp_loc['service-function-forwarder-ovs:ovs-bridge'] = br
                temp_sff_sf_dp_loc['port'] = sfs_dict[sf][dp_loc]['port']
                temp_sff_sf_dp_loc['ip'] = sfs_dict[sf][dp_loc]['ip']

                temp_sf_dict['sff-sf-dataplane-locator'] = temp_sff_sf_dp_loc
                sf_dicts.append(temp_sf_dict)

            # combine sf list into sff dict
            temp_sff = dict({'name': br}.items() + {'sff-data-plane-locator': temp_sff_dp_loc}.items()
                            + {'service-function-forwarder-ovs:ovs-bridge': temp_bridge_dict}.items()
                            + {'service-function-dictionary': sf_dicts}.items())
            sff_list.append(temp_sff)

        sff_dict = {'service-function-forwarder': sff_list}
        sffs_dict = {'service-function-forwarders': sff_dict}
        LOG.debug(_('SFFS dictionary output is %s'), sffs_dict)
        return sffs_dict

    def create_sfc(self, context, sfc):
        """
        :param context:
        :param sfc: dictionary of kwargs from REST request
        :return: dictionary of created object?
        """
        sfc_dict = sfc['sfc']
        LOG.debug(_('chain_dict %s'), sfc_dict)
        # Default driver for SFC is opendaylight
        if 'infra_driver' not in sfc_dict:
            infra_driver = 'opendaylight'
        else:
            infra_driver = sfc_dict['infra_driver']

        dp_loc = 'sf-data-plane-locator'
        sfs_json = dict()
        sf_net_map = dict()
        # Required info for ODL REST call
        # For now assume vxlan and nsh aware
        for sf in sfc_dict['attributes']:
            sf_json = dict()
            sf_json[dp_loc] = dict()
            sf_id = sf
            sf_json['name'] = sf
            sf_json[dp_loc]['name'] = 'vxlan'
            sf_json[dp_loc]['ip'] = sfc_dict['attributes'][sf]['ip']

            if 'port' in sfc_dict['attributes'][sf].keys():
                sf_json[dp_loc]['port'] = sfc_dict['attributes'][sf]['port']
            else:
                sf_json[dp_loc]['port'] = '6633'

            sf_json[dp_loc]['transport'] = 'service-locator:vxlan-gpe'
            # trozet how do we get SFF?
            # may need to ask ODL to find OVS attached to this VNF
            # then create the SFF
            # since this is a chicken and egg problem between SFF, and SF creation
            # we give a dummy value then figure out later
            sf_json[dp_loc]['service-function-forwarder'] = 'dummy'
            sf_json['nsh-aware'] = 'true'
            sf_json['rest-uri'] = "http://%s:%s" % (sf_json[dp_loc]['ip'], sf_json[dp_loc]['port'])
            sf_json['ip-mgmt-address'] = sfc_dict['attributes'][sf]['ip']
            sf_json['type'] = "service-function-type:%s" % (sfc_dict['attributes'][sf]['type'])

            # concat service function json into full dict
            sfs_json = dict(sfs_json.items() + {sf_id: sf_json}.items())

            # map sf id to network id (neutron port)
            sf_net_map[sf] = sfc_dict['attributes'][sf]['neutron_port_id']

        LOG.debug(_('dictionary for sf json:%s'), sfs_json)

        # Locate OVS, ovs_mapping will be a nested dict
        # first key is bridge name, secondary keys sfs list, ovs_ip, sff_name
        ovs_mapping = self.locate_ovs_to_sf(sf_net_map, infra_driver)

        LOG.debug(_('OVS MAP:%s'), ovs_mapping)

        # Go back and update sf SFF
        for br_name in ovs_mapping.keys():
            for sf_id in ovs_mapping[br_name]['sfs']:
                sfs_json[sf_id]['service-function-forwarder'] = ovs_mapping[br_name]['sff_name']
                LOG.debug(_('SF updated with SFF:%s'), ovs_mapping[br_name]['sff_name'])
        # try to create SFs
        service_functions_json = {'service-functions': {}}
        service_functions_json['service-functions'] = {'service-function': list()}
        for (x, y) in sfs_json.items():
            service_functions_json['service-functions']['service-function'].append(y)

        try:
            sfc_result = self._device_manager.invoke(
                infra_driver, 'create_sfs', service_functions_json)
        except Exception:
            LOG.exception(_('Unable to create SFs'))
            return

        # build SFF json
        sff_json = self.create_sff_json(ovs_mapping, sfs_json)
        # try to create SFFs
        try:
            sff_result = self._device_manager.invoke(
                infra_driver, 'create_sff', sff_json)
        except Exception:
            LOG.exception(_('Unable to create SFFs'))
            return

        # try to create SFC

        # try to create SFP

        # try to create RSP

        return

    def create_device(self, context, device):
        device_dict = self._create_device(context, device)

        def create_device_wait():
            self._create_device_wait(context, device_dict)
            self.add_device_to_monitor(device_dict)
            self.config_device(context, device_dict)
        self.spawn_n(create_device_wait)
        return device_dict

    # not for wsgi, but for service to create hosting device
    # the device is NOT added to monitor.
    def create_device_sync(self, context, device):
        device_dict = self._create_device(context, device)
        self._create_device_wait(context, device_dict)
        return device_dict

    def _update_device_wait(self, context, device_dict):
        driver_name = self._infra_driver_name(device_dict)
        instance_id = self._instance_id(device_dict)
        kwargs = {
            mgmt_constants.KEY_ACTION: mgmt_constants.ACTION_UPDATE_DEVICE,
            mgmt_constants.KEY_KWARGS: {'device': device_dict},
        }
        new_status = constants.ACTIVE
        try:
            self._device_manager.invoke(
                driver_name, 'update_wait', plugin=self,
                context=context, device_id=instance_id)
            self.mgmt_call(context, device_dict, kwargs)
        except Exception:
            LOG.exception(_('_update_device_wait'))
            new_status = constants.ERROR
        device_dict['status'] = new_status
        self.mgmt_update_post(context, device_dict)

        self._update_device_post(context, device_dict['id'],
                                 new_status, device_dict)

    def update_device(self, context, device_id, device):
        device_dict = self._update_device_pre(context, device_id)
        driver_name = self._infra_driver_name(device_dict)
        instance_id = self._instance_id(device_dict)

        try:
            self.mgmt_update_pre(context, device_dict)
            self._device_manager.invoke(
                driver_name, 'update', plugin=self, context=context,
                device_id=instance_id, device_dict=device_dict, device=device)
        except Exception:
            with excutils.save_and_reraise_exception():
                device_dict['status'] = constants.ERROR
                self.mgmt_update_post(context, device_dict)
                self._update_device_post(context, device_id, constants.ERROR)

        self.spawn_n(self._update_device_wait, context, device_dict)
        return device_dict

    def _delete_device_wait(self, context, device_dict):
        driver_name = self._infra_driver_name(device_dict)
        instance_id = self._instance_id(device_dict)

        e = None
        try:
            self._device_manager.invoke(
                driver_name, 'delete_wait', plugin=self,
                context=context, device_id=instance_id)
        except Exception as e_:
            e = e_
            device_dict['status'] = constants.ERROR
            LOG.exception(_('_delete_device_wait'))
        self.mgmt_delete_post(context, device_dict)
        device_id = device_dict['id']
        self._delete_device_post(context, device_id, e)

    def delete_device(self, context, device_id):
        device_dict = self._delete_device_pre(context, device_id)
        self._device_status.delete_hosting_device(device_id)
        driver_name = self._infra_driver_name(device_dict)
        instance_id = self._instance_id(device_dict)

        kwargs = {
            mgmt_constants.KEY_ACTION: mgmt_constants.ACTION_DELETE_DEVICE,
            mgmt_constants.KEY_KWARGS: {'device': device_dict},
        }
        try:
            self.mgmt_delete_pre(context, device_dict)
            self.mgmt_call(context, device_dict, kwargs)
            self._device_manager.invoke(driver_name, 'delete', plugin=self,
                                        context=context, device_id=instance_id)
        except Exception as e:
            # TODO(yamahata): when the devaice is already deleted. mask
            # the error, and delete row in db
            # Other case mark error
            with excutils.save_and_reraise_exception():
                device_dict['status'] = constants.ERROR
                self.mgmt_delete_post(context, device_dict)
                self._delete_device_post(context, device_id, e)

        self._delete_device_post(context, device_id, None)
        self.spawn_n(self._delete_device_wait, context, device_dict)

    def _do_interface(self, context, device_id, port_id, action):
        device_dict = self._update_device_pre(context, device_id)
        driver_name = self._infra_driver_name(device_dict)
        instance_id = self._instance_id(device_dict)

        try:
            self._device_manager.invoke(driver_name, action, plugin=self,
                                        context=context, device_id=instance_id)
        except Exception:
            with excutils.save_and_reraise_exception():
                device_dict['status'] = constants.ERROR
                self._update_device_post(context, device_id, constants.ERROR)

        self._update_device_post(context, device_dict['id'], constants.ACTIVE)

    def attach_interface(self, context, id, port_id):
        return self._do_interface(context, id, port_id, 'attach_interface')

    def detach_interface(self, context, id, port_id):
        return self._do_interface(context, id, port_id, 'dettach_interface')

    ###########################################################################
    # logical service instance
    #
    def _create_service_instance_mgmt(
            self, context, device_dict, service_instance_dict):
        kwargs = {
            mgmt_constants.KEY_ACTION: mgmt_constants.ACTION_CREATE_SERVICE,
            mgmt_constants.KEY_KWARGS: {
                'device': device_dict,
                'service_instance': service_instance_dict,
            },
        }
        self.mgmt_call(context, device_dict, kwargs)

        mgmt_driver = self.mgmt_service_driver(
            context, device_dict, service_instance_dict)
        service_instance_dict['mgmt_driver'] = mgmt_driver
        mgmt_url = self.mgmt_service_address(
            context, device_dict, service_instance_dict)
        service_instance_dict['mgmt_url'] = mgmt_url
        LOG.debug(_('service_instance_dict '
                    '%(service_instance_dict)s '
                    'mgmt_driver %(mgmt_driver)s '
                    'mgmt_url %(mgmt_url)s'),
                  {'service_instance_dict':
                   service_instance_dict,
                   'mgmt_driver': mgmt_driver, 'mgmt_url': mgmt_url})
        self._update_service_instance_mgmt(
            context, service_instance_dict['id'], mgmt_driver, mgmt_url)

        self.mgmt_service_create_pre(
            context, device_dict, service_instance_dict)
        self.mgmt_service_call(
            context, device_dict, service_instance_dict, kwargs)

    def _create_service_instance_db(self, context, device_id,
                                    service_instance_param, managed_by_user):
        return super(ServiceVMPlugin, self)._create_service_instance(
            context, device_id, service_instance_param, managed_by_user)

    def _create_service_instance_by_type(
            self, context, device_dict,
            name, service_type, service_table_id):
        LOG.debug(_('device_dict %(device_dict)s '
                    'service_type %(service_type)s'),
                  {'device_dict': device_dict,
                   'service_type': service_type})
        service_type_id = [
            s['id'] for s in
            device_dict['device_template']['service_types']
            if s['service_type'].upper() == service_type.upper()][0]

        service_instance_param = {
            'name': name,
            'service_table_id': service_table_id,
            'service_type': service_type,
            'service_type_id': service_type_id,
        }
        service_instance_dict = self._create_service_instance_db(
            context, device_dict['id'], service_instance_param, False)

        new_status = constants.ACTIVE
        try:
            self._create_service_instance_mgmt(
                context, device_dict, service_instance_dict)
        except Exception:
            LOG.exception(_('_create_service_instance_by_type'))
            new_status = constants.ERROR
            raise
        finally:
            service_instance_dict['status'] = new_status
            self.mgmt_service_create_post(
                context, device_dict, service_instance_dict)
            self._update_service_instance_post(
                context, service_instance_dict['id'], new_status)
        return service_instance_dict

    # for service drivers. e.g. hosting_driver of loadbalancer
    def create_service_instance_by_type(self, context, device_dict,
                                        name, service_type, service_table_id):
        self._update_device_pre(context, device_dict['id'])
        new_status = constants.ACTIVE
        try:
            return self._create_service_instance_by_type(
                context, device_dict, name, service_type,
                service_table_id)
        except Exception:
            LOG.exception(_('create_service_instance_by_type'))
            new_status = constants.ERROR
        finally:
            self._update_device_post(context, device_dict['id'], new_status)

    def _create_service_instance_wait(self, context, device_id,
                                      service_instance_dict):
        device_dict = self.get_device(context, device_id)

        new_status = constants.ACTIVE
        try:
            self._create_service_instance_mgmt(
                context, device_dict, service_instance_dict)
        except Exception:
            LOG.exception(_('_create_service_instance_mgmt'))
            new_status = constants.ERROR
        service_instance_dict['status'] = new_status
        self.mgmt_service_create_post(
            context, device_dict, service_instance_dict)
        self._update_service_instance_post(
            context, service_instance_dict['id'], new_status)

    # for service drivers. e.g. hosting_driver of loadbalancer
    def _create_service_instance(self, context, device_id,
                                 service_instance_param, managed_by_user):
        service_instance_dict = self._create_service_instance_db(
            context, device_id, service_instance_param, managed_by_user)
        self.spawn_n(self._create_service_instance_wait, context,
                     device_id, service_instance_dict)
        return service_instance_dict

    def create_service_instance(self, context, service_instance):
        service_instance_param = service_instance['service_instance'].copy()
        device = service_instance_param.pop('devices')
        device_id = device[0]
        service_instance_dict = self._create_service_instance(
            context, device_id, service_instance_param, True)
        return service_instance_dict

    def _update_service_instance_wait(self, context, service_instance_dict,
                                      mgmt_kwargs, callback, errorback):
        devices = service_instance_dict['devices']
        assert len(devices) == 1
        device_dict = self.get_device(context, devices[0])
        kwargs = {
            mgmt_constants.KEY_ACTION: mgmt_constants.ACTION_UPDATE_SERVICE,
            mgmt_constants.KEY_KWARGS: {
                'device': device_dict,
                'service_instance': service_instance_dict,
                mgmt_constants.KEY_KWARGS: mgmt_kwargs,
            }
        }
        try:
            self.mgmt_call(context, device_dict, kwargs)
            self.mgmt_service_update_pre(context, device_dict,
                                         service_instance_dict)
            self.mgmt_service_call(context, device_dict,
                                   service_instance_dict, kwargs)
        except Exception:
            LOG.exception(_('mgmt call failed %s'), kwargs)
            service_instance_dict['status'] = constants.ERROR
            self.mgmt_service_update_post(context, device_dict,
                                          service_instance_dict)
            self._update_service_instance_post(
                context, service_instance_dict['id'], constants.ERROR)
            if errorback:
                errorback()
        else:
            service_instance_dict['status'] = constants.ACTIVE
            self.mgmt_service_update_post(context, device_dict,
                                          service_instance_dict)
            self._update_service_instance_post(
                context, service_instance_dict['id'], constants.ACTIVE)
            if callback:
                callback()

    # for service drivers. e.g. hosting_driver of loadbalancer
    def _update_service_instance(self, context, service_instance_id,
                                 mgmt_kwargs, callback, errorback):
        service_instance_dict = self._update_service_instance_pre(
            context, service_instance_id, {})
        self.spawn_n(self._update_service_instance_wait, context,
                     service_instance_dict, mgmt_kwargs, callback, errorback)

    # for service drivers. e.g. hosting_driver of loadbalancer
    def _update_service_table_instance(
            self, context, service_table_id, mgmt_kwargs, callback, errorback):
        _device_dict, service_instance_dict = self.get_by_service_table_id(
            context, service_table_id)
        service_instance_dict = self._update_service_instance_pre(
            context, service_instance_dict['id'], {})
        self.spawn_n(self._update_service_instance_wait, context,
                     service_instance_dict, mgmt_kwargs, callback, errorback)

    def update_service_instance(self, context, service_instance_id,
                                service_instance):
        mgmt_kwargs = service_instance['service_instance'].get('kwarg', {})
        service_instance_dict = self._update_service_instance_pre(
            context, service_instance_id, service_instance)

        self.spawn_n(self._update_service_instance_wait, context,
                     service_instance_dict, mgmt_kwargs, None, None)
        return service_instance_dict

    def _delete_service_instance_wait(self, context, device, service_instance,
                                      mgmt_kwargs, callback, errorback):
        service_instance_id = service_instance['id']
        kwargs = {
            mgmt_constants.KEY_ACTION: mgmt_constants.ACTION_DELETE_SERVICE,
            mgmt_constants.KEY_KWARGS: {
                'device': device,
                'service_instance': service_instance,
                mgmt_constants.KEY_KWARGS: mgmt_kwargs,
            }
        }
        try:
            self.mgmt_service_delete_pre(context, device, service_instance)
            self.mgmt_service_call(context, device, service_instance, kwargs)
            self.mgmt_call(context, device, kwargs)
        except Exception:
            LOG.exception(_('mgmt call failed %s'), kwargs)
            service_instance['status'] = constants.ERROR
            self.mgmt_service_delete_post(context, device, service_instance)
            self._update_service_instance_post(context, service_instance_id,
                                               constants.ERROR)
            if errorback:
                errorback()
        else:
            service_instance['status'] = constants.ACTIVE
            self.mgmt_service_delete_post(context, device, service_instance)
            self._delete_service_instance_post(context, service_instance_id)
            if callback:
                callback()

    # for service drivers. e.g. hosting_driver of loadbalancer
    def _delete_service_table_instance(
            self, context, service_table_instance_id,
            mgmt_kwargs, callback, errorback):
        try:
            device, service_instance = self.get_by_service_table_id(
                context, service_table_instance_id)
        except orm_exc.NoResultFound:
            # there are no entry for some reason.
            # e.g. partial creation due to error
            callback()
            return
        self._delete_service_instance_pre(context, service_instance['id'],
                                          False)
        self.spawn_n(
            self._delete_service_instance_wait, context, device,
            service_instance, mgmt_kwargs, callback, errorback)

    def delete_service_instance(self, context, service_instance_id):
        # mgmt_kwargs is needed?
        device, service_instance = self.get_by_service_instance_id(
            context, service_instance_id)
        self._delete_service_instance_pre(context, service_instance_id, True)
        self.spawn_n(
            self._delete_service_instance_wait, context, device,
            service_instance, {}, None, None)
