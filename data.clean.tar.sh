#!/usr/bin/env bash
#
# clean out background files from copies of data/
# then tar them up
#

cat > rm.txt <<EOF 
./inputs/.gitignore
./intermediate/test100_embeddings.parquet
./intermediate/test100_plans.jsonl
./intermediate/test100_rag.jsonl
./out/evaluation/demo.jsonl
./out/plans.jsonl
./out/plans_rules.jsonl
./out/test100_proposals.jsonl
./raw/records.jsonl
EOF

export DIRLIST=$(ls -1d data.v*|grep -v tar)
echo DIRLIST=$DIRLIST

for x in $DIRLIST; do echo "### $x"; pushd $x; cat ../rm.txt | xargs rm  ; popd; tar czf ${x}.tar.gz $x; done
