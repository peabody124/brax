# Copyright 2021 The Brax Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Environment descriptions."""
from brax.experimental.composer import composer_utils
from brax.experimental.composer.components import ant
from brax.experimental.composer.observers import LambdaObserver as lo
from brax.experimental.composer.observers import SimObserver as so

ENV_DESCS = {
    'ant_run':
        dict(
            components=dict(
                agent1=dict(
                    component='ant',
                    pos=(0, 0, 0),
                    reward_fns=dict(
                        goal=dict(
                            reward_type='root_goal',
                            sdcomp='vel',
                            indices=(0, 1),
                            offset=5,
                            target_goal=(4, 0))),
                ),)),
    'ant_chase_ma':
        dict(
            agent_groups=dict(
                agent1=dict(reward_names=('dist_agent1__agent2',)),
                agent2=dict(reward_names=('goal_agent2',)),
            ),
            components=dict(
                agent1=dict(component='ant', pos=(0, 0, 0)),
                agent2=dict(
                    component='ant',
                    pos=(0, 2, 0),
                    reward_fns=dict(
                        goal=dict(
                            reward_type='root_goal',
                            sdcomp='vel',
                            indices=(0, 1),
                            offset=5,
                            scale=1,
                            target_goal=(4, 0)),),
                ),
            ),
            edges=dict(
                agent1__agent2=dict(
                    extra_observers=[
                        dict(observer_type='root_vec', indices=(0, 1)),
                    ],
                    reward_fns=dict(
                        dist=dict(
                            reward_type='root_dist', min_dist=1, offset=5)),
                ),)),
    'ant_chase':
        dict(
            components=dict(
                agent1=dict(component='ant', pos=(0, 0, 0)),
                agent2=dict(
                    component='ant',
                    pos=(0, 2, 0),
                    reward_fns=dict(
                        goal=dict(
                            reward_type='root_goal',
                            sdcomp='vel',
                            indices=(0, 1),
                            offset=5,
                            scale=1,
                            target_goal=(4, 0)),),
                ),
            ),
            edges=dict(
                agent1__agent2=dict(
                    extra_observers=[
                        dict(observer_type='root_vec', indices=(0, 1)),
                    ],
                    reward_fns=dict(
                        dist=dict(
                            reward_type='root_dist', min_dist=1, offset=5)),
                ),)),
    'ant_push':
        dict(
            components=dict(
                agent1=dict(
                    component='ant',
                    pos=(0, 0, 0),
                ),
                cap1=dict(
                    component='singleton',
                    component_params=dict(size=0.5),
                    pos=(1, 0, 0),
                    observers=('root_z_joints',),
                    reward_fns=dict(
                        goal=dict(
                            reward_type='root_goal',
                            sdcomp='vel',
                            indices=(0, 1),
                            offset=5,
                            scale=1,
                            target_goal=5)),
                ),
            ),
            edges=dict(
                agent1__cap1=dict(
                    extra_observers=[
                        dict(observer_type='root_vec', indices=(0, 1)),
                    ],
                    reward_fns=dict(
                        dist=dict(reward_type='root_dist', offset=5)),
                ),)),
    'uni_ant':
        dict(components=dict(agent1=dict(component='ant', pos=(0, 0, 0)),),),
    'uni_octopus':
        dict(
            components=dict(agent1=dict(component='octopus', pos=(0, 0, 0)),),),
    'bi_ant':
        dict(
            components=dict(
                agent1=dict(component='ant', pos=(0, 1, 0)),
                agent2=dict(component='ant', pos=(0, -1, 0)),
            ),
            extra_observers=[
                lo(name='delta_pos',
                   fn='-',
                   observers=[
                       so('body', 'pos', ant.ROOT, 'agent1'),
                       so('body', 'pos', ant.ROOT, 'agent2')
                   ]),
                lo(name='delta_vel',
                   fn='-',
                   observers=[
                       so('body', 'vel', ant.ROOT, 'agent1'),
                       so('body', 'vel', ant.ROOT, 'agent2')
                   ]),
            ],
            edges=dict(agent1__agent2=dict(collide_type=None),)),
    'tri_ant':
        dict(
            components=dict(
                agent1=dict(component='ant', pos=(0, 1, 0)),
                agent2=dict(component='ant', pos=(0, -1, 0)),
                agent3=dict(component='ant', pos=(1, 0, 0)),
            ),
            edges=dict(),
        ),
    'ant_on_ball':
        dict(
            global_options=dict(dt=0.02, substeps=16),
            components=dict(
                agent1=dict(
                    component='pro_ant',
                    component_params=dict(num_legs=4),
                    pos=(0, 0, 6),
                    term_params=dict(z_offset=6),
                    reward_fns=dict(
                        goal=dict(
                            reward_type='root_goal',
                            sdcomp='vel',
                            indices=(0, 1),
                            offset=4,
                            target_goal=(3, 0))),
                ),
                cap1=dict(
                    component='singleton',
                    component_params=dict(size=3),
                    pos=(0, 0, 0),
                    observers=('root_z_joints',),
                ),
            ),
            edges=dict(
                agent1__cap1=dict(
                    extra_observers=[
                        dict(observer_type='root_vec', indices=(0, 1)),
                    ],),),
        )
}

VARIANTS = (
    ('ant_run', 'pro_ant_run', {
        'components.agent1.component': 'pro_ant',
        'components.agent1.component_params': dict(num_legs=10),
        'global_options.dt': 0.02,
        'global_options.substeps': 16,
    }),
    ('ant_run', 'octopus_run', {
        'components.agent1.component': 'octopus',
        'global_options.dt': 0.02,
        'global_options.substeps': 16,
    }),
)

for base_desc_name, new_desc_name, desc_edits in VARIANTS:
  ENV_DESCS[new_desc_name] = composer_utils.edit_desc(ENV_DESCS[base_desc_name],
                                                      desc_edits)
