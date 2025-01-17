# Copyright 2023 Google Inc.
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

import os
import sys
from absl import logging
import pandas as pd
import constants

# For importing util
_CODEDIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(1, os.path.join(_CODEDIR, "../"))

from util import dc_client as dc


# TODO: Add support for units.
class SimpleStatsImporter:

    def __init__(
            self,
            input_path: str,
            output_dir: str,
            entity_type: str,
            ignore_columns: list[str] = list(),
    ) -> None:
        self.input_path = input_path
        self.output_dir = output_dir
        self.observations_file = os.path.join(output_dir,
                                              constants.OBSERVATIONS_FILE_NAME)
        self.debug_resolve_file = os.path.join(
            output_dir, constants.DEBUG_RESOLVE_FILE_NAME)
        self.entity_type = entity_type
        self.ignore_columns = ignore_columns
        self.df = pd.DataFrame()
        self.debug_resolve_df = None

    def do_import(self) -> None:
        self._init()
        self._read_csvs()
        self._drop_ignored_columns()
        self._rename_columns()
        self._resolve_entities()

        if constants.UNPIVOT_VARIABLES:
            self._unpivot_variables()
            self._reorder_columns()

        self._write_csvs()

    def _init(self):
        os.makedirs(self.output_dir, exist_ok=True)

    def _read_csvs(self) -> None:
        if os.path.isdir(self.input_path):
            self.df = SimpleStatsImporter._read_csvs_from_dir(self.input_path)
        else:
            self.df = pd.read_csv(self.input_path)

        logging.info("Read %s rows.", self.df.index.size)

    @staticmethod
    def _read_csvs_from_dir(input_dir: str) -> pd.DataFrame:
        files = [
            os.path.join(input_dir, filename)
            for filename in os.listdir(input_dir)
        ]
        df = pd.DataFrame()
        for file in files:
            df = pd.concat([df, pd.read_csv(file)])
        return df

    def _drop_ignored_columns(self):
        if self.ignore_columns:
            self.df.drop(columns=self.ignore_columns, axis=1, inplace=True)

    def _rename_columns(self) -> None:
        df = self.df
        df.columns.values[0] = constants.COLUMN_DCID
        if constants.UNPIVOT_VARIABLES:
            df.columns.values[1] = constants.COLUMN_DATE

    def _resolve_entities(self) -> None:
        df = self.df
        # get first (0th) column
        column = df.iloc[:, 0]

        pre_resolved_entities = {}

        def remove_pre_resolved(entity: str) -> bool:
            if entity.startswith(constants.DCID_OVERRIDE_PREFIX):
                pre_resolved_entities[entity] = entity[
                    len(constants.DCID_OVERRIDE_PREFIX):].strip()
                return False
            return True

        entities = list(filter(remove_pre_resolved, column.tolist()))

        logging.info("Found %s entities pre-resolved.",
                     len(pre_resolved_entities))

        logging.info("Resolving %s entities of type %s.", len(entities),
                     self.entity_type)
        dcids = dc.resolve_entities(entities=entities,
                                    entity_type=self.entity_type)
        logging.info("Resolved %s of %s entities.", len(dcids), len(entities))

        # Replace resolved entities.
        column.replace(dcids, inplace=True)
        unresolved = set(entities).difference(set(dcids.keys()))
        unresolved_list = list(unresolved)

        # Replace pre-resolved entities without the "dcid:" prefix.
        column.replace(pre_resolved_entities, inplace=True)

        if unresolved_list:
            logging.warning("# unresolved entities which will be dropped: %s",
                            len(unresolved_list))
            logging.warning("Dropped entities: %s", unresolved_list)
            df.drop(df[df.iloc[:, 0].isin(values=unresolved_list)].index,
                    inplace=True)
        self._create_debug_resolve_dataframe(
            resolved=dcids,
            pre_resolved=pre_resolved_entities,
            unresolved=unresolved_list,
        )

    def _create_debug_resolve_dataframe(
        self,
        resolved: dict[str, str],
        pre_resolved: dict[str, str],
        unresolved: list[str],
    ):
        # Add unresolved names first
        names = unresolved[:]
        dcids = [constants.DEBUG_UNRESOLVED_DCID] * len(unresolved)

        # Add pre-resolved next.
        names.extend(list(pre_resolved.keys()))
        dcids.extend(list(pre_resolved.values()))

        # Add resolved names and dcids
        names.extend(list(resolved.keys()))
        dcids.extend(list(resolved.values()))

        # Create browser links
        links = []
        for dcid in dcids:
            if dcid == constants.DEBUG_UNRESOLVED_DCID:
                links.append("")
            else:
                links.append(f"{constants.DC_BROWSER}/{dcid}")

        # Create dataframe
        self.debug_resolve_df = pd.DataFrame({
            constants.DEBUG_COLUMN_NAME: names,
            constants.DEBUG_COLUMN_DCID: dcids,
            constants.DEBUG_COLUMN_LINK: links,
        })

    def _unpivot_variables(self) -> None:
        self.df = self.df.melt(
            id_vars=[constants.COLUMN_DCID, constants.COLUMN_DATE],
            var_name=constants.COLUMN_VARIABLE,
            value_name=constants.COLUMN_VALUE,
        ).dropna()

    def _reorder_columns(self) -> None:
        self.df = self.df.reindex(columns=[
            constants.COLUMN_DCID,
            constants.COLUMN_VARIABLE,
            constants.COLUMN_DATE,
            constants.COLUMN_VALUE,
        ])

    def _write_csvs(self) -> None:
        logging.info("Writing %s observations to: %s", self.df.index.size,
                     self.observations_file)
        self.df.to_csv(self.observations_file, index=False)
        if self.debug_resolve_df is not None:
            logging.info("Writing resolutions (for debugging) to: %s",
                         self.debug_resolve_file)
            self.debug_resolve_df.to_csv(self.debug_resolve_file, index=False)
